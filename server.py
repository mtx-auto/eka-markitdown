# stdlib
import io
import json
import os
import tempfile
from pathlib import Path
from typing import Annotated

# third-party
import httpx
from fastmcp import FastMCP
from markitdown import MarkItDown
from pydantic import Field

mcp = FastMCP(
    "eka-markitdown",
    instructions=(
        "Convert documents (PDF, PowerPoint, Word, Excel, HTML, EPUB, images, "
        "audio, CSV, JSON, XML, ZIP) to Markdown using Microsoft MarkItDown. "
        "Accepts public URLs and DIAL file references."
    ),
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DIAL_CORE_BASE_URL = os.environ.get("DIAL_CORE_BASE_URL", "https://core.aks.dev.dial.parts")
AUTH_HEADER_NAMES = ["Api-Key"]

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def _forwarded_headers() -> dict:
    """Extract forwarded auth headers from the incoming MCP request.

    ASGI/Starlette lowercases all incoming header names, so we match
    case-insensitively against AUTH_HEADER_NAMES.
    """
    from fastmcp.server.dependencies import get_http_headers

    incoming = {k.lower(): v for k, v in get_http_headers().items()}
    return {name: incoming[name.lower()] for name in AUTH_HEADER_NAMES if name.lower() in incoming}


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------
async def _download_from_url(url: str) -> tuple[bytes, str]:
    """Download a file from a public URL.

    Returns:
        Tuple of (raw bytes, content-type string).

    Raises:
        httpx.HTTPError: If the download fails.
    """
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "application/octet-stream")
        return resp.content, content_type


async def _download_from_dial(file_path: str) -> tuple[bytes, str]:
    """Download a file from DIAL Core using the forwarded per-request Api-Key.

    Args:
        file_path: A DIAL resource path, e.g. ``files/private/report.pdf``.

    Returns:
        Tuple of (raw bytes, content-type string).

    Raises:
        ValueError: If no auth headers are available or the download fails.
    """
    headers = _forwarded_headers()
    if not headers:
        raise ValueError(
            "No forwarded auth headers available. The DIAL toolset must have "
            "forward_per_request_key enabled."
        )

    # DIAL Core file download URL: /v1/<bucket>/<path>
    download_url = f"{DIAL_CORE_BASE_URL}/v1/{file_path}"

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(download_url, headers=headers)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "application/octet-stream")
        return resp.content, content_type


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------
def _convert_bytes(content: bytes, filename: str) -> str:
    """Convert raw file bytes to Markdown using MarkItDown.

    Writes the content to a temp file so MarkItDown can infer the format
    from the file extension. The temp file is cleaned up after conversion.

    Args:
        content: Raw file bytes.
        filename: Original filename (used to determine the file extension).

    Returns:
        The Markdown-converted content as a string.

    Raises:
        ValueError: If the format is unsupported or conversion fails.
    """
    suffix = Path(filename).suffix or ""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        md = MarkItDown()
        result = md.convert(tmp_path)
        return result.markdown
    except Exception as e:
        raise ValueError(f"Conversion failed for {filename}: {e}") from e
    finally:
        if tmp_path is not None:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool
async def list_supported_formats() -> str:
    """List all file formats supported by MarkItDown with descriptions.

    Returns:
        A formatted JSON object listing each supported format, its file
        extensions, and a brief description.
    """
    formats = {
        "documents": {
            "pdf": {
                "extensions": [".pdf"],
                "description": "Portable Document Format — text extraction and layout analysis",
            },
            "powerpoint": {
                "extensions": [".pptx"],
                "description": "Microsoft PowerPoint presentations",
            },
            "word": {
                "extensions": [".docx"],
                "description": "Microsoft Word documents",
            },
            "excel": {
                "extensions": [".xlsx", ".xls"],
                "description": "Microsoft Excel spreadsheets (including legacy XLS)",
            },
        },
        "web_and_text": {
            "html": {
                "extensions": [".html", ".htm"],
                "description": "HyperText Markup Language — web pages",
            },
            "epub": {
                "extensions": [".epub"],
                "description": "Electronic Publication — e-book format",
            },
            "csv": {
                "extensions": [".csv"],
                "description": "Comma-Separated Values — tabular data",
            },
            "json": {
                "extensions": [".json"],
                "description": "JavaScript Object Notation — structured data",
            },
            "xml": {
                "extensions": [".xml"],
                "description": "eXtensible Markup Language — structured data",
            },
            "plain_text": {
                "extensions": [".txt"],
                "description": "Plain text files",
            },
        },
        "multimedia": {
            "images": {
                "extensions": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"],
                "description": "Image files — EXIF metadata extraction and OCR text recognition",
            },
            "audio": {
                "extensions": [".mp3", ".wav", ".m4a", ".ogg", ".flac"],
                "description": "Audio files — EXIF metadata extraction and speech transcription",
            },
        },
        "archives": {
            "zip": {
                "extensions": [".zip"],
                "description": "ZIP archives — extracted and contents converted individually",
            },
        },
        "other": {
            "youtube": {
                "extensions": ["(URL)"],
                "description": "YouTube video URLs — video metadata and transcript",
            },
        },
    }
    return json.dumps(formats, indent=2)


@mcp.tool
async def convert_file(
    source: Annotated[
        str,
        Field(
            description=(
                "An HTTP(S) URL pointing to a document file, or a DIAL file "
                "path (e.g. ``files/private/report.pdf`` or "
                "``files/public/notes.docx``). The file extension is used to "
                "determine the conversion method. For DIAL paths, pass the "
                "path exactly as given — do not inline its bytes or "
                "base64-encode it."
            ),
            json_schema_extra={"dial_url": True},
        ),
    ],
) -> str:
    """Convert a document to Markdown format.

    Accepts either a public HTTP(S) URL or a DIAL file path (``files/...``).
    Downloads the file and converts it using Microsoft MarkItDown. Supported
    formats: PDF, PowerPoint, Word, Excel, HTML, EPUB, images (EXIF + OCR),
    audio (EXIF + speech), CSV, JSON, XML, ZIP archives, and YouTube URLs.

    Args:
        source: An HTTP(S) URL or a DIAL file path (``files/<bucket>/<path>``).

    Returns:
        The Markdown-converted content of the document.

    Raises:
        ValueError: If the source cannot be downloaded or the format is not
            supported.
    """
    # Handle inline content passed via file:data:: or file:base64:: prefix
    if source.startswith("file:data::"):
        # data URI — extract the base64 after the comma
        _, data_part = source.split("::", 1)
        if "," in data_part:
            data_part = data_part.split(",", 1)[1]
        import base64
        content = base64.b64decode(data_part)
        # Try to determine filename from content type in data URI
        filename = "document"
        if ";" in source:
            ext_map = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg",
                       "text/html": ".html", "text/plain": ".txt", "application/json": ".json"}
            for mime, ext in ext_map.items():
                if mime in source:
                    filename = f"document{ext}"
                    break
        return _convert_bytes(content, filename)

    if source.startswith("file:base64::"):
        # plain base64 content
        _, content_b64 = source.split("::", 1)
        import base64
        content = base64.b64decode(content_b64)
        return _convert_bytes(content, "document")

    if source.startswith("file:text::"):
        # raw text content
        _, text = source.split("::", 1)
        return _convert_bytes(text.encode("utf-8"), "document.txt")

    if source.startswith(("http://", "https://")):
        try:
            content, _ = await _download_from_url(source)
        except httpx.HTTPError as e:
            raise ValueError(f"Failed to download {source}: {e}") from e
        filename = source.rstrip("/").split("/")[-1] or "document"
    elif source.startswith("files/"):
        try:
            content, _ = await _download_from_dial(source)
        except httpx.HTTPError as e:
            raise ValueError(
                f"Failed to download {source} from DIAL Core: {e}"
            ) from e
        filename = source.rstrip("/").split("/")[-1] or "document"
    else:
        raise ValueError(
            "source must be an HTTP(S) URL or a DIAL file path (starting with 'files/')."
        )

    return _convert_bytes(content, filename)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    app = mcp.http_app(host_origin_protection=False)
    uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")
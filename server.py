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
async def convert_file(source: str) -> str:
    """Convert a document from a URL to Markdown format.

    Downloads the file from the given URL and converts it using Microsoft
    MarkItDown. Supported formats: PDF, PowerPoint, Word, Excel, HTML, EPUB,
    images (EXIF + OCR), audio (EXIF + speech), CSV, JSON, XML, ZIP archives,
    and YouTube URLs.

    Args:
        source: An HTTP(S) URL pointing to a document file. The file extension
            is used to determine the conversion method.

    Returns:
        The Markdown-converted content of the document.

    Raises:
        ValueError: If the URL cannot be downloaded or the format is not
            supported.
    """
    if not source.startswith(("http://", "https://")):
        raise ValueError(
            "source must be an HTTP or HTTPS URL. "
            "Use convert_file_from_dial for DIAL file references."
        )

    try:
        content, _ = await _download_from_url(source)
    except httpx.HTTPError as e:
        raise ValueError(f"Failed to download {source}: {e}") from e

    filename = source.rstrip("/").split("/")[-1] or "document"
    return _convert_bytes(content, filename)


@mcp.tool
async def convert_file_from_dial(
    file_path: Annotated[
        str,
        Field(
            description=(
                "A DIAL file path (e.g. ``files/private/report.pdf`` or "
                "``files/public/notes.docx`` — no scheme or host; pass this "
                "path exactly as given, do not inline its bytes or base64-encode "
                "it). The file will be downloaded from DIAL Core and converted "
                "to Markdown."
            ),
            json_schema_extra={"dial_url": True},
        ),
    ],
) -> str:
    """Convert a DIAL file attachment to Markdown format.

    Downloads the file from DIAL Core using the forwarded authentication and
    converts it using Microsoft MarkItDown. Supports the same formats as
    convert_file.

    Args:
        file_path: A DIAL file path (e.g. ``files/private/report.pdf``). Must
            start with ``files/``.

    Returns:
        The Markdown-converted content of the document.

    Raises:
        ValueError: If the path is invalid, auth fails, download fails, or
            conversion fails.
    """
    if not file_path.startswith("files/"):
        raise ValueError(
            "file_path must start with 'files/'. "
            "Use convert_file for HTTP URLs."
        )

    try:
        content, _ = await _download_from_dial(file_path)
    except httpx.HTTPError as e:
        raise ValueError(f"Failed to download {file_path} from DIAL Core: {e}") from e

    filename = file_path.rstrip("/").split("/")[-1] or "document"
    return _convert_bytes(content, filename)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    app = mcp.http_app(host_origin_protection=False)
    uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")
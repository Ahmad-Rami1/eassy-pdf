from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Query
from pydantic import BaseModel
from fastapi.responses import FileResponse
import tempfile
from weasyprint import HTML
import subprocess
import os
import glob
import shutil
import io
import logging
import re

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()


class GeneratePDFRequest(BaseModel):
    html_content: str


@app.post("/generate-pdf/")
async def generate_pdf(request: Request):
    html_content = await request.body()
    html_content = html_content.decode("utf-8")  # Decode bytes to string
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmpfile:
            HTML(string=html_content).write_pdf(tmpfile.name)
            return FileResponse(path=tmpfile.name, media_type='application/pdf', filename="generated_document.pdf")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/flatten-pdf/")
async def flatten_pdf(file: UploadFile = File(...)):
    """
    Fully rasterize the uploaded PDF with high compression while preserving zoom level:
    1) Extract page dimensions from original PDF
    2) Convert PDF to low-resolution JPEG images
    3) Combine images back into a compressed PDF with original dimensions
    """
    # --------------------
    # 1. Save the input PDF to a temp file
    # --------------------
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_in:
            content = await file.read()
            tmp_in.write(content)
            input_pdf = tmp_in.name
            logger.info(f"Saved input PDF to {input_pdf}")
    except Exception as e:
        logger.error(f"Failed to save input PDF: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Could not write input PDF: {e}")

    # --------------------
    # 2. Extract page dimensions from the original PDF
    # --------------------
    page_dimensions = []
    try:
        # Use pdfinfo to get page dimensions
        cmd = ["pdfinfo", "-box", input_pdf]
        try:
            pdfinfo_output = subprocess.check_output(
                cmd, universal_newlines=True)

            # Extract page sizes
            page_info = pdfinfo_output.split("Page size:")
            if len(page_info) > 1:
                size_line = page_info[1].split("\n")[0].strip()
                # Parse something like "595.32 x 841.92 pts (A4)"
                match = re.search(r'(\d+\.?\d*)\s*x\s*(\d+\.?\d*)', size_line)
                if match:
                    width_pts = float(match.group(1))
                    height_pts = float(match.group(2))
                    page_dimensions = [width_pts, height_pts]
                    logger.info(
                        f"Extracted page dimensions: {width_pts} x {height_pts} pts")
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.warning("pdfinfo not available, trying alternative method")

            # Try using pdftk to get page size
            try:
                cmd = ["pdftk", input_pdf, "dump_data"]
                pdftk_output = subprocess.check_output(
                    cmd, universal_newlines=True)

                # Find page size information
                for line in pdftk_output.split("\n"):
                    if "PageMediaDimensions" in line:
                        parts = line.split()
                        if len(parts) >= 3:
                            width_pts = float(parts[1])
                            height_pts = float(parts[2])
                            page_dimensions = [width_pts, height_pts]
                            logger.info(
                                f"Extracted page dimensions: {width_pts} x {height_pts} pts")
                            break
            except (subprocess.CalledProcessError, FileNotFoundError):
                logger.warning("pdftk not available, using standard A4 size")
                # Default to A4 size if extraction fails
                page_dimensions = [595.28, 841.89]  # A4 size in points
    except Exception as e:
        logger.warning(f"Failed to extract page dimensions: {str(e)}")
        # Default to A4 size if extraction fails
        page_dimensions = [595.28, 841.89]  # A4 size in points

    # --------------------
    # 3. Create a temporary directory to hold image pages
    # --------------------
    img_dir = tempfile.mkdtemp()
    logger.info(f"Created temporary directory: {img_dir}")

    # Use JPEG for better compression with lower quality
    img_format = "jpeg"
    img_pattern = os.path.join(img_dir, "page-%03d.jpg")

    # --------------------
    # 4. Convert PDF -> images with low resolution and high compression
    # --------------------
    try:
        # Use pdftoppm from Poppler instead of Ghostscript (more reliable)
        try:
            # Check if pdftoppm is available
            subprocess.check_call(["which", "pdftoppm"])

            # Use pdftoppm with high compression settings
            cmd = [
                "pdftoppm",
                "-jpeg",      # Output format
                "-r", "150",  # Lower resolution (150 DPI)
                "-jpegopt", "quality=50",  # Lower JPEG quality for higher compression
                input_pdf,
                os.path.join(img_dir, "page")  # Output filename prefix
            ]
            logger.info(f"Running pdftoppm command: {' '.join(cmd)}")
            subprocess.check_call(cmd)

        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fall back to Ghostscript if pdftoppm is not available
            logger.info("pdftoppm not available, falling back to Ghostscript")
            cmd = [
                "gs",
                "-q",  # Quiet mode
                "-dNOPAUSE",
                "-dBATCH",
                f"-sDEVICE={img_format}",
                "-dJPEGQ=50",  # Reduced JPEG quality for higher compression
                "-r150",  # Lower resolution (150 DPI)
                "-o", img_pattern,
                input_pdf
            ]
            logger.info(f"Running Ghostscript command: {' '.join(cmd)}")
            subprocess.check_call(cmd)

    except subprocess.CalledProcessError as e:
        logger.error(f"Image conversion failed: {str(e)}")
        cleanup(input_pdf, img_dir)
        raise HTTPException(
            status_code=500, detail=f"PDF to image conversion failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error during conversion: {str(e)}")
        cleanup(input_pdf, img_dir)
        raise HTTPException(status_code=500, detail=str(e))

    # --------------------
    # 5. Gather all image files
    # --------------------
    img_files = []
    patterns = [
        os.path.join(img_dir, "page-*.jpg"),
        os.path.join(img_dir, "page*.jpg"),  # pdftoppm format
    ]

    for pattern in patterns:
        found_files = glob.glob(pattern)
        if found_files:
            img_files = sorted(found_files)
            break

    if not img_files:
        logger.error("No image files found after conversion")
        cleanup(input_pdf, img_dir)
        raise HTTPException(
            status_code=500, detail="No image pages produced. PDF might be invalid or empty.")

    logger.info(f"Found {len(img_files)} image files")

    # --------------------
    # 6. Rebuild PDF from images with original dimensions
    # --------------------
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_out:
            output_pdf = tmp_out.name

        try:
            # Try direct img2pdf approach - simpler and more reliable
            import img2pdf
            from PIL import Image
            logger.info("Using basic img2pdf approach")

            with open(output_pdf, "wb") as f:
                f.write(img2pdf.convert(img_files))

            # Check if file is valid
            if os.path.getsize(output_pdf) < 100:
                raise ValueError("Output PDF appears to be empty")

        except Exception as e:
            logger.info(f"img2pdf method failed: {str(e)}")

            # Try reportlab with correct imports
            try:
                from reportlab.pdfgen import canvas
                from reportlab.lib.units import inch
                import PyPDF2

                logger.info("Using reportlab/PyPDF2 to create output PDF")

                # Create temporary PDFs for each image with reportlab
                temp_pdfs = []
                for i, img_path in enumerate(img_files):
                    img = Image.open(img_path)
                    img_width, img_height = img.size

                    # Create a temporary PDF for this image
                    temp_pdf = os.path.join(img_dir, f"temp_page_{i}.pdf")
                    temp_pdfs.append(temp_pdf)

                    # Create PDF with the exact dimensions
                    c = canvas.Canvas(temp_pdf, pagesize=page_dimensions)

                    # Calculate scaling to fit image to page
                    scale_x = page_dimensions[0] / img_width
                    scale_y = page_dimensions[1] / img_height
                    scale = min(scale_x, scale_y)

                    # Center the image
                    x = (page_dimensions[0] - img_width * scale) / 2
                    y = (page_dimensions[1] - img_height * scale) / 2

                    # Draw the image
                    c.drawImage(img_path, x, y, width=img_width *
                                scale, height=img_height * scale)
                    c.save()

                # Merge all PDFs with PyPDF2
                merger = PyPDF2.PdfMerger()
                for pdf in temp_pdfs:
                    with open(pdf, 'rb') as f:
                        merger.append(f)

                with open(output_pdf, 'wb') as f:
                    merger.write(f)

                # Verify file is not empty
                if os.path.getsize(output_pdf) < 100:
                    raise ValueError("Output PDF appears to be empty")

            except Exception as e:
                logger.info(f"reportlab/PyPDF2 method failed: {str(e)}")

                # Try creating a simple PDF with ImageMagick's convert
                try:
                    logger.info("Using ImageMagick convert approach")

                    # First convert each JPEG to PDF individually
                    pdf_files = []
                    for i, img_path in enumerate(img_files):
                        pdf_file = os.path.join(img_dir, f"image_{i}.pdf")
                        pdf_files.append(pdf_file)

                        # Convert single image to PDF with correct dimensions
                        cmd = [
                            "convert",
                            img_path,
                            "-page", f"{page_dimensions[0]}x{page_dimensions[1]}",
                            pdf_file
                        ]
                        subprocess.check_call(cmd)

                    # Then merge the PDFs
                    cmd = ["pdfunite"] + pdf_files + [output_pdf]
                    subprocess.check_call(cmd)

                    # Verify file is not empty
                    if os.path.getsize(output_pdf) < 100:
                        raise ValueError("Output PDF appears to be empty")

                except Exception as e:
                    logger.error(f"All methods failed: {str(e)}")
                    raise

    except subprocess.CalledProcessError as e:
        logger.error(f"PDF creation failed: {str(e)}")
        cleanup(input_pdf, img_dir)
        if os.path.exists(output_pdf):
            os.remove(output_pdf)
        raise HTTPException(
            status_code=500, detail=f"PDF creation failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error during PDF creation: {str(e)}")
        cleanup(input_pdf, img_dir)
        if os.path.exists(output_pdf):
            os.remove(output_pdf)
        raise HTTPException(status_code=500, detail=str(e))

    # --------------------
    # 7. Return the fully rasterized PDF
    # --------------------
    logger.info(f"Returning output PDF: {output_pdf}")
    cleanup(input_pdf, img_dir)
    return FileResponse(
        path=output_pdf,
        media_type="application/pdf",
        filename="rasterized.pdf"
    )


def cleanup(input_pdf: str, img_dir: str):
    """ Helper to remove the input PDF and image directory. """
    if os.path.exists(input_pdf):
        os.remove(input_pdf)
    if os.path.isdir(img_dir):
        shutil.rmtree(img_dir, ignore_errors=True)


@app.get("/hello")
async def hello_world():
    return {"message": "Hello World!"}


def save_temp(stream: io.BytesIO):
    """ Helper to save in-memory PDF to a temp file and return its path. """
    import tempfile
    import os
    tmpfile = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmpfile.write(stream.read())
    tmpfile.flush()
    tmpfile.close()
    stream.seek(0)  # reset if needed
    return tmpfile.name

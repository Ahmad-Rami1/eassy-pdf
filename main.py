from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from pydantic import BaseModel
from fastapi.responses import FileResponse
import tempfile
from weasyprint import HTML
import subprocess
import os
import glob
import shutil
import io

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
    Fully rasterize the uploaded PDF using Ghostscript:
      1) Convert each PDF page to a PNG image at a chosen resolution.
      2) Rebuild a PDF from those PNG images.

    This completely removes all text layers, form fields,
    and interactive elements — guaranteeing 100% flattening.

    Downsides:
      • Text is no longer selectable/searchable.
      • File size may grow if resolution is high.
    """
    # --------------------
    # 1. Save the input PDF to a temp file
    # --------------------
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_in:
            tmp_in.write(await file.read())
            input_pdf = tmp_in.name
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Could not write input PDF: {e}")

    # --------------------
    # 2. Create a temporary directory to hold PNG pages
    # --------------------
    png_dir = tempfile.mkdtemp()
    png_pattern = os.path.join(png_dir, "page-%03d.png")

    # --------------------
    # 3. Convert PDF -> PNG images
    #    Ghostscript flags explained:
    #      -sDEVICE=png16m    -> produce 24-bit color PNG
    #      -r300             -> 300 DPI (adjust for your desired quality vs. file size)
    #      -o "page-%03d.png"-> auto-number each page image
    #      -dNOPAUSE -dBATCH -> run without interactive prompts
    # --------------------
    cmd1 = [
        "gs",
        "-sDEVICE=png16m",
        "-r300",  # Increased resolution for better quality
        "-o", png_pattern,
        "-dNOPAUSE",
        "-dBATCH",
        input_pdf
    ]
    try:
        subprocess.check_call(cmd1)
    except subprocess.CalledProcessError as e:
        cleanup(input_pdf, png_dir)
        raise HTTPException(
            status_code=500, detail=f"Ghostscript (PDF->PNG) failed: {e}")
    except Exception as e:
        cleanup(input_pdf, png_dir)
        raise HTTPException(status_code=500, detail=str(e))

    # --------------------
    # 4. Gather all PNG page files
    # --------------------
    png_files = sorted(glob.glob(os.path.join(png_dir, "page-*.png")))
    if not png_files:
        cleanup(input_pdf, png_dir)
        raise HTTPException(
            status_code=500, detail="No PNG pages produced (PDF might be invalid).")

    # --------------------
    # 5. Alternative approach: Use img2pdf instead of Ghostscript
    #    img2pdf is more reliable for converting images to PDF
    # --------------------
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_out:
            output_pdf = tmp_out.name

        # Use img2pdf library if available (needs to be installed)
        try:
            import img2pdf
            with open(output_pdf, "wb") as f:
                f.write(img2pdf.convert(png_files))
        except ImportError:
            # Fallback to a more careful Ghostscript approach
            cmd2 = [
                "gs",
                "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.4",
                "-dPDFSETTINGS=/prepress",  # Higher quality
                "-dAutoFilterColorImages=false",
                "-dColorImageFilter=/FlateEncode",  # Use lossless compression
                "-dNOPAUSE",
                "-dBATCH",
                "-o", output_pdf,
            ]

            # Add each PNG file individually to avoid issues with binary data
            for png_file in png_files:
                cmd2.append(png_file)

            subprocess.check_call(cmd2)

    except subprocess.CalledProcessError as e:
        cleanup(input_pdf, png_dir)
        if os.path.exists(output_pdf):
            os.remove(output_pdf)
        raise HTTPException(
            status_code=500, detail=f"PDF creation failed: {e}")
    except Exception as e:
        cleanup(input_pdf, png_dir)
        if os.path.exists(output_pdf):
            os.remove(output_pdf)
        raise HTTPException(status_code=500, detail=str(e))

    # --------------------
    # 6. Return the fully rasterized PDF
    # --------------------
    # We'll clean up input and PNG files now.
    cleanup(input_pdf, png_dir)
    return FileResponse(
        path=output_pdf,
        media_type="application/pdf",
        filename="rasterized.pdf"
    )


def cleanup(input_pdf: str, png_dir: str):
    """ Helper to remove the input PDF and PNG directory. """
    if os.path.exists(input_pdf):
        os.remove(input_pdf)
    if os.path.isdir(png_dir):
        shutil.rmtree(png_dir, ignore_errors=True)


# @app.post("/merge-pdfs/")
# async def merge_pdfs(files: list[UploadFile] = File(...)):
#     """
#     Merge multiple PDFs into one and return the merged PDF.
#     """
#     merger = PdfMerger()
#     try:
#         for file in files:
#             # Convert uploaded file to an in-memory stream
#             pdf_bytes = await file.read()
#             pdf_stream = io.BytesIO(pdf_bytes)
#             merger.append(pdf_stream)

#         # Output merged PDF to memory
#         output_stream = io.BytesIO()
#         merger.write(output_stream)
#         merger.close()
#         output_stream.seek(0)

#         return FileResponse(
#             path=save_temp(output_stream),
#             media_type="application/pdf",
#             filename="merged.pdf"
#         )
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))


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

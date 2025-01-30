from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from pydantic import BaseModel
from fastapi.responses import FileResponse
import tempfile
from weasyprint import HTML
import subprocess
import os
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
    Receive a single PDF file, flatten it with Ghostscript, then return the flattened PDF.
    """
    try:
        # 1. Save the uploaded PDF to a temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as input_tmp:
            input_tmp.write(await file.read())
            input_path = input_tmp.name

        # 2. Create a temp file path for the flattened PDF
        output_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        output_path = output_tmp.name
        output_tmp.close()  # We just need the path

        # 3. Run Ghostscript to flatten the PDF
        #    -sDEVICE=pdfwrite is standard for PDF output
        #    -dPDFSETTINGS=/prepress helps maintain high quality
        #    -dCompatibilityLevel=1.4 ensures a widely compatible PDF
        #    For more advanced flags, see Ghostscript docs
        cmd = [
            "gs",
            "-o", output_path,
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            "-dPDFSETTINGS=/prepress",
            input_path
        ]
        subprocess.check_call(cmd)

        # 4. Return the flattened PDF as a FileResponse
        return FileResponse(
            path=output_path,
            media_type="application/pdf",
            filename="flattened.pdf"
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Ghostscript failed: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Cleanup
        if os.path.exists(input_path):
            os.remove(input_path)
        # We remove output_path later in FileResponse's background tasks,
        # or do it after returning if you prefer storing in memor


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

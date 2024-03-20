from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from fastapi.responses import FileResponse
import tempfile
from weasyprint import HTML

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


@app.get("/hello")
async def hello_world():
    return {"message": "Hello World!"}

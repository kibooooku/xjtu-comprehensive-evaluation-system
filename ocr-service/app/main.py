from fastapi import FastAPI

app = FastAPI(
    title="XJTU Evaluation OCR Service",
    version="0.1.0",
    description="Extracts document text and coordinates; it does not make business decisions.",
)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ocr-service"}

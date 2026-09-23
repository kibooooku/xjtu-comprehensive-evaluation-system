package edu.xjtu.evaluation.pdftext;

public record DocumentTextBlock(int pageNumber, String text, double x, double y,
        double width, double height, Source source) {
    public enum Source { PDF_TEXT, OCR }
}

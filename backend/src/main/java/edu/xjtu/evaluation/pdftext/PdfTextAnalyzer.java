package edu.xjtu.evaluation.pdftext;

import java.io.IOException;
import java.io.StringWriter;
import java.util.ArrayList;
import java.util.List;

import org.apache.pdfbox.Loader;
import org.apache.pdfbox.io.IOUtils;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.pdmodel.PDPage;
import org.apache.pdfbox.pdmodel.common.PDRectangle;
import org.apache.pdfbox.text.PDFTextStripper;
import org.apache.pdfbox.text.TextPosition;
import org.springframework.stereotype.Component;

@Component
public class PdfTextAnalyzer {
    private static final int MAX_PAGES_TO_ANALYZE = 200;
    private static final int MAX_TEXT_BLOCKS = 100_000;

    public Analysis analyze(byte[] bytes) {
        Integer pageCount = null;
        try (PDDocument pdf = Loader.loadPDF(bytes, "", null, null,
                IOUtils.createTempFileOnlyStreamCache())) {
            pageCount = pdf.getNumberOfPages();
            if (pageCount < 1 || pageCount > MAX_PAGES_TO_ANALYZE)
                return new Analysis(pageCount > 0 ? pageCount : null, Status.FAILED, List.of());
            List<DocumentTextBlock> blocks = new ArrayList<>();
            PDFTextStripper stripper = new PDFTextStripper() {
                private int pageNumber;
                private PDPage page;
                private int positionsSeen;
                @Override protected void processTextPosition(TextPosition position) {
                    // PDFTextStripper buffers/sorts TextPositions before writeString().
                    // Reject oversized text layers before that buffer can grow without bound.
                    if (++positionsSeen > MAX_TEXT_BLOCKS) throw new TextLimitExceeded();
                    super.processTextPosition(position);
                }
                @Override protected void startPage(PDPage next) throws IOException {
                    page = next;
                    pageNumber++;
                    super.startPage(next);
                }
                @Override protected void writeString(String text, List<TextPosition> positions) throws IOException {
                    for (TextPosition position : positions) {
                        String unicode = position.getUnicode();
                        if (unicode == null || unicode.isBlank()) continue;
                        if (blocks.size() >= MAX_TEXT_BLOCKS) throw new IOException("PDF text limit exceeded");
                        DocumentTextBlock block = block(pageNumber, page, position, unicode);
                        if (block != null) blocks.add(block);
                    }
                }
            };
            stripper.setSortByPosition(true);
            stripper.writeText(pdf, new StringWriter());
            return new Analysis(pageCount, blocks.isEmpty() ? Status.NO_TEXT : Status.TEXT_AVAILABLE,
                    List.copyOf(blocks));
        } catch (IOException | RuntimeException failure) {
            // A broken or unsupported text layer must never prevent existing manual annotation.
            return new Analysis(pageCount, Status.FAILED, List.of());
        }
    }

    private DocumentTextBlock block(int pageNumber, PDPage page, TextPosition position, String text) {
        PDRectangle crop = page.getCropBox();
        double pageWidth = crop.getWidth(), pageHeight = crop.getHeight();
        if (pageWidth <= 0 || pageHeight <= 0) return null;
        // Direction-adjusted positions undo the page /Rotate and crop offset.
        // They match the unrotated PDF.js viewport used by EvidenceEditor.
        // getWidth() can be zero for a 90-degree page; use direction-adjusted width.
        double x = position.getXDirAdj();
        double y = position.getYDirAdj() - position.getHeightDir();
        double w = position.getWidthDirAdj();
        double h = position.getHeightDir();
        double left = clamp(x / pageWidth), top = clamp(y / pageHeight);
        double right = clamp((x + w) / pageWidth), bottom = clamp((y + h) / pageHeight);
        double nx = six(left), ny = six(top), nw = six(right) - nx, nh = six(bottom) - ny;
        if (nw <= 0 || nh <= 0) return null;
        return new DocumentTextBlock(pageNumber, text, nx, ny, nw, nh,
                DocumentTextBlock.Source.PDF_TEXT);
    }
    private double clamp(double value) { return Math.max(0, Math.min(1, value)); }
    private double six(double value) { return Math.round(value * 1_000_000d) / 1_000_000d; }

    private static final class TextLimitExceeded extends RuntimeException {}
    public enum Status { TEXT_AVAILABLE, NO_TEXT, FAILED }
    public record Analysis(Integer pageCount, Status status, List<DocumentTextBlock> blocks) {}
}

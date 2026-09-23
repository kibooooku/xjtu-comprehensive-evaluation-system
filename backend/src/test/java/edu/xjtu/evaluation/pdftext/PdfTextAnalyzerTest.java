package edu.xjtu.evaluation.pdftext;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.pdmodel.PDPage;
import org.apache.pdfbox.pdmodel.PDPageContentStream;
import org.apache.pdfbox.pdmodel.common.PDRectangle;
import org.apache.pdfbox.pdmodel.font.PDType1Font;
import org.apache.pdfbox.pdmodel.font.Standard14Fonts;
import org.junit.jupiter.api.Test;

class PdfTextAnalyzerTest {
    private final PdfTextAnalyzer analyzer = new PdfTextAnalyzer();

    @Test void extractsNativeTextAndCountsPages() throws Exception {
        var result = analyzer.analyze(pdf(false, false));
        assertThat(result.status()).isEqualTo(PdfTextAnalyzer.Status.TEXT_AVAILABLE);
        assertThat(result.pageCount()).isEqualTo(2);
        assertThat(result.blocks()).extracting(DocumentTextBlock::text).contains("2", "4", "0", "1");
        assertThat(result.blocks()).allSatisfy(block -> {
            assertThat(block.source()).isEqualTo(DocumentTextBlock.Source.PDF_TEXT);
            assertThat(block.pageNumber()).isEqualTo(1);
            assertThat(block.x()).isBetween(0d, 1d);
            assertThat(block.y()).isBetween(0d, 1d);
        });
    }

    @Test void blankScanAndBrokenFileFallBackWithoutText() throws Exception {
        var blank = analyzer.analyze(pdf(true, false));
        assertThat(blank.pageCount()).isEqualTo(2);
        assertThat(blank.status()).isEqualTo(PdfTextAnalyzer.Status.NO_TEXT);
        assertThat(blank.blocks()).isEmpty();
        var broken = analyzer.analyze("%PDF-1.4\ninvalid".getBytes());
        assertThat(broken.status()).isEqualTo(PdfTextAnalyzer.Status.FAILED);
        assertThat(broken.blocks()).isEmpty();
    }

    @Test void rotatedCroppedPageProducesBoundedNormalizedCoordinates() throws Exception {
        for (int rotation : new int[]{0,90,180,270}) {
            byte[] bytes = pdf(false, rotation);
            try (PDDocument raw = org.apache.pdfbox.Loader.loadPDF(bytes)) {
                assertThat(new org.apache.pdfbox.text.PDFTextStripper().getText(raw)).contains("2");
            }
            var result = analyzer.analyze(bytes);
            assertThat(result.status()).describedAs("rotation %s", rotation)
                    .isEqualTo(PdfTextAnalyzer.Status.TEXT_AVAILABLE);
            assertThat(result.blocks()).isNotEmpty().allSatisfy(block -> {
                assertThat(block.x()).isBetween(0d, 1d);
                assertThat(block.y()).isBetween(0d, 1d);
                assertThat(block.width()).isPositive();
                assertThat(block.height()).isPositive();
                assertThat(block.x() + block.width()).isLessThanOrEqualTo(1d);
                assertThat(block.y() + block.height()).isLessThanOrEqualTo(1d);
            });
            var first = result.blocks().getFirst();
            assertThat(first.x()).describedAs("rotation %s horizontal crop position", rotation)
                    .isCloseTo((80d - 30d) / 340d, org.assertj.core.data.Offset.offset(.005d));
            assertThat(first.y()).describedAs("rotation %s vertical crop position", rotation)
                    .isCloseTo((280d - 170d) / 260d, org.assertj.core.data.Offset.offset(.04d));
        }
    }
    @Test void oversizedNativeTextLayerFallsBackBeforeSortingAllPositions() throws Exception {
        try (PDDocument document = new PDDocument()) {
            PDPage page = new PDPage(new PDRectangle(400, 300));
            document.addPage(page);
            try (PDPageContentStream stream = new PDPageContentStream(document, page)) {
                stream.beginText();
                stream.setFont(new PDType1Font(Standard14Fonts.FontName.HELVETICA), .002f);
                stream.newLineAtOffset(30, 200);
                stream.showText("A".repeat(100_001));
                stream.endText();
            }
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            document.save(output);
            var result = analyzer.analyze(output.toByteArray());
            assertThat(result.pageCount()).isEqualTo(1);
            assertThat(result.status()).isEqualTo(PdfTextAnalyzer.Status.FAILED);
            assertThat(result.blocks()).isEmpty();
        }
    }
    static byte[] pdf(boolean blank, boolean rotated) throws IOException { return pdf(blank, rotated ? 90 : 0); }
    static byte[] pdf(boolean blank, int rotation) throws IOException {
        try (PDDocument document = new PDDocument()) {
            PDPage page = new PDPage(new PDRectangle(400, 300));
            {
                page.setCropBox(new PDRectangle(30, 20, 340, 260));
                page.setRotation(rotation);
            }
            document.addPage(page);
            if (!blank) {
                try (PDPageContentStream stream = new PDPageContentStream(document, page)) {
                    stream.beginText();
                    stream.setFont(new PDType1Font(Standard14Fonts.FontName.HELVETICA), 14);
                    stream.newLineAtOffset(80, 170);
                    stream.showText("2400000001 Alex Example");
                    stream.endText();
                }
            }
            document.addPage(new PDPage(new PDRectangle(400, 300)));
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            document.save(output);
            return output.toByteArray();
        }
    }
}

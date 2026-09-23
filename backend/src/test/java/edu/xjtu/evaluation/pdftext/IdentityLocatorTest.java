package edu.xjtu.evaluation.pdftext;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.Test;

class IdentityLocatorTest {
    private final IdentityLocator locator = new IdentityLocator();

    @Test void exactNumberWinsOverNameAndCandidateIdsAreStable() {
        var blocks = List.of(block(1, "Alex Example", .1), block(1, "2400000001", .5));
        var found = locator.locate("2400000001", "Alex Example", blocks);
        assertThat(found).hasSize(1);
        assertThat(found.getFirst().matchedBy()).isEqualTo(IdentityLocator.MatchedBy.STUDENT_NUMBER);
        assertThat(found.getFirst().matchedText()).isEqualTo("2400000001");
        assertThat(locator.locate("2400000001", "Alex Example", blocks).getFirst().id())
                .isEqualTo(found.getFirst().id());
    }

    @Test void fallsBackToNameAndFlagsMultipleMatches() {
        var found = locator.locate("9999999999", "Alex Example", List.of(
                block(1, "Alex Example", .1), block(2, "Alex Example", .3)));
        assertThat(found).hasSize(2).extracting(IdentityLocator.Candidate::certainty)
                .containsOnly(IdentityLocator.Certainty.MULTIPLE);
        assertThat(found).extracting(IdentityLocator.Candidate::matchedBy)
                .containsOnly(IdentityLocator.MatchedBy.NAME);
        assertThat(found.get(0).id()).isNotEqualTo(found.get(1).id());
    }

    @Test void numberEmbeddedInLongerNumberDoesNotMatch() {
        var found = locator.locate("2400000001", "Alex Example", List.of(
                block(1, "92400000019", .1), block(1, "Alex Example", .5)));
        assertThat(found).hasSize(1);
        assertThat(found.getFirst().matchedBy()).isEqualTo(IdentityLocator.MatchedBy.NAME);
    }

    @Test void rejectsStudentNumberInsideAsciiIdentifierButAllowsChineseLabels() {
        var embedded = locator.locate("2400000001", "Alex Example", List.of(
                block(1, "A2400000001B", .1), block(2, "Alex Example", .1)));
        assertThat(embedded).hasSize(1);
        assertThat(embedded.getFirst().matchedBy()).isEqualTo(IdentityLocator.MatchedBy.NAME);
        var labeled = locator.locate("2400000001", "Alex Example", List.of(
                block(1, "学号2400000001姓名", .1), block(2, "Alex Example", .1)));
        assertThat(labeled).hasSize(1);
        assertThat(labeled.getFirst().matchedBy()).isEqualTo(IdentityLocator.MatchedBy.STUDENT_NUMBER);
    }

    @Test void findsLastPageAmongManyDenseLines() {
        java.util.List<DocumentTextBlock> blocks = new java.util.ArrayList<>();
        for (int page = 1; page <= 100; page++) {
            for (int line = 0; line < 20; line++) {
                String text = page == 100 && line == 19 ? "2400000001" : "fictional";
                blocks.add(new DocumentTextBlock(page, text, .1, line * .04, .2, .02,
                        DocumentTextBlock.Source.PDF_TEXT));
            }
        }
        var found = locator.locate("2400000001", "Alex Example", blocks);
        assertThat(found).hasSize(1);
        assertThat(found.getFirst().pageNumber()).isEqualTo(100);
    }
    private DocumentTextBlock block(int page, String text, double x) {
        return new DocumentTextBlock(page, text, x, .2, .2, .04, DocumentTextBlock.Source.PDF_TEXT);
    }
}

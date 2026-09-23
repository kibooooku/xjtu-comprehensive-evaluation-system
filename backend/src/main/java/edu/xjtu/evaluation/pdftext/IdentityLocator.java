package edu.xjtu.evaluation.pdftext;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import org.springframework.stereotype.Component;

@Component
public class IdentityLocator {
    public List<Candidate> locate(String studentNumber, String name, List<DocumentTextBlock> blocks) {
        String number = studentNumber == null ? "" : studentNumber.trim();
        String studentName = name == null ? "" : name.trim();
        if (!number.isEmpty()) {
            List<Candidate> byNumber = matches(number, MatchedBy.STUDENT_NUMBER, blocks);
            if (!byNumber.isEmpty()) return certainty(byNumber);
        }
        return studentName.isEmpty() ? List.of() : certainty(matches(studentName, MatchedBy.NAME, blocks));
    }

    private List<Candidate> matches(String query, MatchedBy matchedBy, List<DocumentTextBlock> blocks) {
        List<DocumentTextBlock> ordered = blocks.stream()
                .filter(block -> block.source() == DocumentTextBlock.Source.PDF_TEXT)
                .sorted(Comparator.comparingInt(DocumentTextBlock::pageNumber)
                        .thenComparingDouble(DocumentTextBlock::y).thenComparingDouble(DocumentTextBlock::x))
                .toList();
        List<Line> lines = new ArrayList<>();
        for (DocumentTextBlock block : ordered) {
            Line line = null;
            for (int i = lines.size() - 1; i >= 0; i--) {
                Line prior = lines.get(i);
                if (prior.pageNumber < block.pageNumber() || prior.y < block.y() - 0.006) break;
                if (prior.pageNumber == block.pageNumber() && Math.abs(prior.y - block.y()) <= 0.006) {
                    line = prior;
                    break;
                }
            }
            if (line == null) {
                line = new Line(block.pageNumber(), block.y());
                lines.add(line);
            }
            line.blocks.add(block);
        }
        List<Candidate> found = new ArrayList<>();
        Set<String> seen = new LinkedHashSet<>();
        for (Line line : lines) {
            line.blocks.sort(Comparator.comparingDouble(DocumentTextBlock::x));
            StringBuilder text = new StringBuilder();
            List<DocumentTextBlock> positions = new ArrayList<>();
            DocumentTextBlock previous = null;
            for (DocumentTextBlock block : line.blocks) {
                if (previous != null && block.x() - previous.x() - previous.width() >
                        Math.max(0.003, Math.min(previous.width(), block.width()) * 0.35)) {
                    text.append(' ');
                    positions.add(null);
                }
                text.append(block.text());
                for (int i = 0; i < block.text().length(); i++) positions.add(block);
                previous = block;
            }
            int start = 0;
            while ((start = text.indexOf(query, start)) >= 0) {
                int end = start + query.length();
                boolean token = matchedBy != MatchedBy.STUDENT_NUMBER ||
                        ((start == 0 || !isAsciiIdentifier(text.charAt(start - 1))) &&
                         (end == text.length() || !isAsciiIdentifier(text.charAt(end))));
                if (token) {
                    List<DocumentTextBlock> hit = positions.subList(start, end).stream()
                            .filter(java.util.Objects::nonNull).toList();
                    if (!hit.isEmpty()) {
                        double left = hit.stream().mapToDouble(DocumentTextBlock::x).min().orElse(0);
                        double top = hit.stream().mapToDouble(DocumentTextBlock::y).min().orElse(0);
                        double right = hit.stream().mapToDouble(b -> b.x() + b.width()).max().orElse(0);
                        double bottom = hit.stream().mapToDouble(b -> b.y() + b.height()).max().orElse(0);
                        double x = six(Math.max(0, left - 0.006)), y = six(Math.max(0, top - 0.008));
                        double r = six(Math.min(1, right + 0.006)), b = six(Math.min(1, bottom + 0.008));
                        String key = line.pageNumber + ":" + x + ":" + y + ":" + r + ":" + b;
                        if (r > x && b > y && seen.add(key)) found.add(new Candidate("",line.pageNumber,x,y,
                                six(r - x),six(b - y),query,matchedBy,DocumentTextBlock.Source.PDF_TEXT,Certainty.HIGH));
                    }
                }
                start++;
            }
        }
        return found;
    }
    private List<Candidate> certainty(List<Candidate> candidates) {
        Certainty level = candidates.size() == 1 ? Certainty.HIGH : Certainty.MULTIPLE;
        List<Candidate> result = new ArrayList<>();
        for (int i = 0; i < candidates.size(); i++) {
            Candidate c = candidates.get(i);
            result.add(new Candidate(candidateId(c),c.pageNumber(),c.x(),c.y(),c.width(),
                    c.height(),c.matchedText(),c.matchedBy(),c.source(),level));
        }
        return result;
    }
    private boolean isAsciiIdentifier(char value) {
        return (value >= '0' && value <= '9') || (value >= 'A' && value <= 'Z')
                || (value >= 'a' && value <= 'z');
    }
    private String candidateId(Candidate candidate) {
        String identity = candidate.pageNumber() + "|" + candidate.x() + "|" + candidate.y()
                + "|" + candidate.width() + "|" + candidate.height() + "|"
                + candidate.matchedBy() + "|" + candidate.matchedText();
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(identity.getBytes(StandardCharsets.UTF_8));
            return "candidate-" + HexFormat.of().formatHex(digest, 0, 16);
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }
    private double six(double value) {return Math.round(value * 1_000_000d) / 1_000_000d;}
    private static final class Line {
        final int pageNumber;
        final double y;
        final List<DocumentTextBlock> blocks = new ArrayList<>();
        Line(int pageNumber,double y) {this.pageNumber=pageNumber;this.y=y;}
    }
    public enum MatchedBy { STUDENT_NUMBER, NAME }
    public enum Certainty { HIGH, MULTIPLE }
    public record Candidate(String id,int pageNumber,double x,double y,double width,double height,
            String matchedText,MatchedBy matchedBy,DocumentTextBlock.Source source,Certainty certainty) {}
}

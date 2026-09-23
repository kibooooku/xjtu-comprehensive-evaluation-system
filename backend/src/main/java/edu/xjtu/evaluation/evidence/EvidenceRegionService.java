package edu.xjtu.evaluation.evidence;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.security.Principal;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import edu.xjtu.evaluation.pdftext.IdentityLocator.Candidate;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;

import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@Service
public class EvidenceRegionService {
    private static final BigDecimal ZERO = BigDecimal.ZERO;
    private static final BigDecimal ONE = BigDecimal.ONE;
    private final JdbcClient jdbc;

    public EvidenceRegionService(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional(readOnly = true)
    public List<Region> list(Principal principal, long declarationId) {
        long documentId = ownedDocumentId(principal, declarationId, false, null);
        return jdbc.sql("""
                SELECT id, document_id, type, page_number, x, y, width, height, source, created_at, updated_at
                FROM evidence_region WHERE document_id=:documentId ORDER BY page_number,id
                """).param("documentId", documentId).query(this::map).list();
    }

    @Transactional
    public Region create(Principal principal, long declarationId, long expectedVersion, RegionInput input) {
        long documentId = ownedDocumentId(principal, declarationId, true, expectedVersion);
        Coordinates c = coordinates(input);
        validatePage(documentId, input.pageNumber());
        try {
            var key = new org.springframework.jdbc.support.GeneratedKeyHolder();
            jdbc.sql("""
                    INSERT INTO evidence_region(document_id,type,page_number,x,y,width,height,source)
                    VALUES(:documentId,:type,:page,:x,:y,:width,:height,'MANUAL')
                    """).param("documentId", documentId).param("type", input.type().name())
                    .param("page", input.pageNumber()).param("x", c.x()).param("y", c.y())
                    .param("width", c.width()).param("height", c.height()).update(key, "id");
            return requireRegion(key.getKey().longValue(), documentId);
        } catch (DataIntegrityViolationException e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid evidence region", e);
        }
    }

    @Transactional
    public Region update(Principal principal, long declarationId, long regionId, long expectedVersion, RegionInput input) {
        long documentId = ownedDocumentId(principal, declarationId, true, expectedVersion);
        Coordinates c = coordinates(input);
        validatePage(documentId, input.pageNumber());
        try {
            int count = jdbc.sql("""
                    UPDATE evidence_region SET type=:type,page_number=:page,x=:x,y=:y,width=:width,
                        height=:height,updated_at=CURRENT_TIMESTAMP
                    WHERE id=:regionId AND document_id=:documentId
                    """).param("type", input.type().name()).param("page", input.pageNumber())
                    .param("x", c.x()).param("y", c.y()).param("width", c.width()).param("height", c.height())
                    .param("regionId", regionId).param("documentId", documentId).update();
            if (count == 0) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Evidence region not found");
            return requireRegion(regionId, documentId);
        } catch (DataIntegrityViolationException e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid evidence region", e);
        }
    }

    @Transactional
    public void delete(Principal principal, long declarationId, long regionId, long expectedVersion) {
        long documentId = ownedDocumentId(principal, declarationId, true, expectedVersion);
        int count = jdbc.sql("DELETE FROM evidence_region WHERE id=:regionId AND document_id=:documentId")
                .param("regionId", regionId).param("documentId", documentId).update();
        if (count == 0) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Evidence region not found");
    }

    @Transactional
    public Region confirmCandidate(Principal principal, long declarationId, long expectedVersion, Candidate candidate) {
        long documentId = ownedDocumentId(principal, declarationId, true, expectedVersion);
        if (candidate == null || candidate.pageNumber() < 1) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "候选区域无效");
        }
        validatePage(documentId, candidate.pageNumber());
        RegionInput input = new RegionInput(Type.IDENTITY, candidate.pageNumber(),
                BigDecimal.valueOf(candidate.x()), BigDecimal.valueOf(candidate.y()),
                BigDecimal.valueOf(candidate.width()), BigDecimal.valueOf(candidate.height()));
        Coordinates c = coordinates(input);
        var key = new org.springframework.jdbc.support.GeneratedKeyHolder();
        jdbc.sql("""
                INSERT INTO evidence_region(document_id,type,page_number,x,y,width,height,source)
                VALUES(:documentId,'IDENTITY',:page,:x,:y,:width,:height,'PDF_TEXT_AUTO')
                """).param("documentId",documentId).param("page",candidate.pageNumber())
                .param("x",c.x()).param("y",c.y()).param("width",c.width()).param("height",c.height())
                .update(key,"id");
        return requireRegion(key.getKey().longValue(),documentId);
    }

    private void validatePage(long documentId, int pageNumber) {
        Integer pageCount = jdbc.sql("SELECT page_count FROM declaration_pdf WHERE id=:id")
                .param("id",documentId).query(Integer.class).optional().orElse(null);
        if (pageCount != null && pageNumber > pageCount)
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"页码超出当前 PDF 页数");
    }
    private long ownedDocumentId(Principal principal, long declarationId, boolean lock, Long expectedVersion) {
        String permission = lock
                ? "u.username=:username AND u.enabled=TRUE AND d.status='DRAFT'"
                : """
                  (u.username=:username OR (d.status<>'DRAFT' AND EXISTS (
                      SELECT 1 FROM class_membership reviewer
                      JOIN app_user reader ON reader.id=reviewer.user_id
                      WHERE reviewer.class_id=cm.class_id AND reviewer.role='CLASS_COMMITTEE'
                        AND reader.username=:username AND reader.enabled=TRUE
                  )))
                  """;
        String sql = """
                SELECT d.id FROM declaration d
                JOIN class_membership cm ON cm.id=d.class_membership_id
                JOIN app_user u ON u.id=cm.user_id
                WHERE d.id=:declarationId AND
                """ + permission + (lock ? " FOR UPDATE" : "");
        boolean owned = jdbc.sql(sql).param("declarationId", declarationId)
                .param("username", principal.getName()).query(Long.class).optional().isPresent();
        if (!owned) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Declaration not found");
        DocumentVersion document = jdbc.sql(
                "SELECT id,document_version FROM declaration_pdf WHERE declaration_id=:declarationId")
                .param("declarationId", declarationId)
                .query((rs, n) -> new DocumentVersion(rs.getLong("id"), rs.getLong("document_version")))
                .optional()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.CONFLICT, "A PDF must be uploaded first"));
        if (expectedVersion != null && document.version() != expectedVersion) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "PDF was replaced; reload before editing evidence");
        }
        return document.id();
    }

    private Coordinates coordinates(RegionInput input) {
        if (input == null || input.type() == null || input.pageNumber() == null
                || input.x() == null || input.y() == null || input.width() == null || input.height() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Region fields are required");
        }
        if (input.pageNumber() < 1) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Page number must be positive");
        if (!fitsPage(input.x(), input.y(), input.width(), input.height())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Normalized coordinates must fit within the page");
        }
        BigDecimal x = rounded(input.x()), y = rounded(input.y());
        BigDecimal width = rounded(input.width()), height = rounded(input.height());
        if (x.compareTo(ZERO) < 0 || x.compareTo(ONE) >= 0
                || y.compareTo(ZERO) < 0 || y.compareTo(ONE) >= 0
                || width.compareTo(ZERO) <= 0 || width.compareTo(ONE) > 0
                || height.compareTo(ZERO) <= 0 || height.compareTo(ONE) > 0
                || x.add(width).compareTo(ONE) > 0 || y.add(height).compareTo(ONE) > 0) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Normalized coordinates must fit within the page");
        }
        return new Coordinates(x, y, width, height);
    }

    private boolean fitsPage(BigDecimal x, BigDecimal y, BigDecimal width, BigDecimal height) {
        return x.compareTo(ZERO) >= 0 && x.compareTo(ONE) < 0
                && y.compareTo(ZERO) >= 0 && y.compareTo(ONE) < 0
                && width.compareTo(ZERO) > 0 && width.compareTo(ONE) <= 0
                && height.compareTo(ZERO) > 0 && height.compareTo(ONE) <= 0
                && x.add(width).compareTo(ONE) <= 0
                && y.add(height).compareTo(ONE) <= 0;
    }
    private BigDecimal rounded(BigDecimal value) {
        return value.setScale(6, RoundingMode.HALF_UP);
    }

    private Region requireRegion(long regionId, long documentId) {
        return jdbc.sql("""
                SELECT id, document_id, type, page_number, x, y, width, height, source, created_at, updated_at
                FROM evidence_region WHERE id=:regionId AND document_id=:documentId
                """).param("regionId", regionId).param("documentId", documentId)
                .query(this::map).optional()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Evidence region not found"));
    }

    private Region map(java.sql.ResultSet rs, int row) throws java.sql.SQLException {
        return new Region(rs.getLong("id"), rs.getLong("document_id"), Type.valueOf(rs.getString("type")),
                rs.getInt("page_number"), rs.getBigDecimal("x"), rs.getBigDecimal("y"),
                rs.getBigDecimal("width"), rs.getBigDecimal("height"), Source.valueOf(rs.getString("source")),
                toInstant(rs.getTimestamp("created_at")), toInstant(rs.getTimestamp("updated_at")));
    }

    private Instant toInstant(Timestamp timestamp) {
        return timestamp.toInstant();
    }

    public enum Type { IDENTITY, VALIDITY }
    public enum Source { MANUAL, OCR, PDF_TEXT_AUTO }
    public record RegionInput(@NotNull Type type, @NotNull Integer pageNumber,
            @NotNull BigDecimal x, @NotNull BigDecimal y,
            @NotNull BigDecimal width, @NotNull BigDecimal height) {}
    public record Region(long id, long documentId, Type type, int pageNumber,
            BigDecimal x, BigDecimal y, BigDecimal width, BigDecimal height,
            Source source, Instant createdAt, Instant updatedAt) {}
    private record Coordinates(BigDecimal x, BigDecimal y, BigDecimal width, BigDecimal height) {}
    private record DocumentVersion(long id, long version) {}
}

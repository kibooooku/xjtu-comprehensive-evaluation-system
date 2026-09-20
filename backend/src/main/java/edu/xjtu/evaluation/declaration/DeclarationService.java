package edu.xjtu.evaluation.declaration;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.security.MessageDigest;
import java.security.Principal;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

import edu.xjtu.evaluation.storage.FileStorageService;

@Service
public class DeclarationService {
    private static final byte[] PDF_SIGNATURE = {'%', 'P', 'D', 'F', '-'};
    private final JdbcClient jdbc;
    private final FileStorageService storage;
    private final long maxBytes;

    public DeclarationService(JdbcClient jdbc, FileStorageService storage,
            @Value("${app.storage.max-pdf-bytes:10485760}") long maxBytes) {
        this.jdbc = jdbc;
        this.storage = storage;
        this.maxBytes = maxBytes;
    }

    @Transactional
    public DeclarationView create(Principal principal, CreateRequest request) {
        long userId = userId(principal);
        long membershipId = jdbc.sql("SELECT id FROM class_membership WHERE user_id=:uid AND class_id=:cid")
                .param("uid", userId).param("cid", request.classId()).query(Long.class).optional()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.FORBIDDEN, "Not a member of this class"));
        KeyHolder keyHolder = new GeneratedKeyHolder();
        jdbc.sql("INSERT INTO declaration(class_membership_id,title,status) VALUES(:mid,:title,'DRAFT')")
                .param("mid", membershipId).param("title", request.title().trim())
                .update(keyHolder, "id");
        long id = keyHolder.getKey().longValue();
        return owned(id, userId);
    }

    @Transactional(readOnly = true)
    public List<DeclarationView> mine(Principal principal) {
        return queryOwned(" ORDER BY d.created_at DESC,d.id DESC", userId(principal), null, false);
    }

    @Transactional(readOnly = true)
    public DeclarationView get(Principal principal, long id) {
        return queryOwned(" AND d.id=:id", userId(principal), id, true).stream().findFirst()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Declaration not found"));
    }

    @Transactional
    public DeclarationView updateTitle(Principal principal, long id, String title) {
        long userId = userId(principal);
        lockOwnedDraft(id, userId);
        String normalized = title == null ? "" : title.trim();
        if (normalized.isBlank() || normalized.length() > 200) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Title must be 1-200 characters");
        }
        jdbc.sql("UPDATE declaration SET title=:title,updated_at=CURRENT_TIMESTAMP WHERE id=:id")
                .param("title", normalized).param("id", id).update();
        return owned(id, userId);
    }
    @Transactional
    public DeclarationView upload(Principal principal, long id, MultipartFile file) {
        long userId = userId(principal);
        lockOwnedDraft(id, userId);
        if (pdfExists(id)) throw new ResponseStatusException(HttpStatus.CONFLICT, "PDF already uploaded");
        byte[] bytes = validate(file);
        String key = "declarations/" + id + "/" + UUID.randomUUID() + ".pdf";
        String filename = safeFilename(file.getOriginalFilename());
        String sha = sha256(bytes);
        try {
            storage.store(key, new ByteArrayInputStream(bytes));
            deleteIfTransactionRollsBack(key);
            jdbc.sql("""
                    INSERT INTO declaration_pdf(declaration_id,original_filename,media_type,size_bytes,storage_key,sha256)
                    VALUES(:id,:name,'application/pdf',:size,:key,:sha)
                    """).param("id", id).param("name", filename).param("size", bytes.length)
                    .param("key", key).param("sha", sha).update();
        } catch (DataIntegrityViolationException e) {
            deleteQuietly(key);
            throw new ResponseStatusException(HttpStatus.CONFLICT, "PDF already uploaded", e);
        } catch (IOException e) {
            deleteQuietly(key);
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "Could not store PDF", e);
        } catch (RuntimeException e) {
            deleteQuietly(key);
            throw e;
        }
        return owned(id, userId);
    }
    @Transactional
    public DeclarationView replacePdf(Principal principal, long id, MultipartFile file) {
        long userId = userId(principal);
        lockOwnedDraft(id, userId);
        ExistingPdf previous = jdbc.sql("SELECT id,storage_key FROM declaration_pdf WHERE declaration_id=:id")
                .param("id", id)
                .query((rs, n) -> new ExistingPdf(rs.getLong("id"), rs.getString("storage_key")))
                .optional()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.CONFLICT, "No PDF to replace"));

        byte[] bytes = validate(file);
        String newKey = "declarations/" + id + "/" + UUID.randomUUID() + ".pdf";
        String filename = safeFilename(file.getOriginalFilename());
        String sha = sha256(bytes);
        try {
            storage.store(newKey, new ByteArrayInputStream(bytes));
            deleteIfTransactionRollsBack(newKey);
            jdbc.sql("DELETE FROM evidence_region WHERE document_id=:documentId")
                    .param("documentId", previous.id()).update();
            jdbc.sql("""
                    UPDATE declaration_pdf
                    SET original_filename=:name,media_type='application/pdf',size_bytes=:size,
                        storage_key=:key,sha256=:sha,document_version=document_version+1,created_at=CURRENT_TIMESTAMP
                    WHERE id=:documentId
                    """).param("name", filename).param("size", bytes.length).param("key", newKey)
                    .param("sha", sha).param("documentId", previous.id()).update();
            deleteAfterCommit(previous.key());
        } catch (IOException e) {
            deleteQuietly(newKey);
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "Could not store PDF", e);
        } catch (RuntimeException e) {
            deleteQuietly(newKey);
            throw e;
        }
        return owned(id, userId);
    }

    @Transactional(readOnly = true)
    public Download download(Principal principal, long id) {
        long userId = userId(principal);
        StoredPdf pdf = jdbc.sql("""
                SELECT p.storage_key,p.original_filename,p.size_bytes FROM declaration_pdf p
                JOIN declaration d ON d.id=p.declaration_id
                JOIN class_membership cm ON cm.id=d.class_membership_id
                WHERE p.declaration_id=:id AND (
                    cm.user_id=:uid OR (
                        d.status<>'DRAFT' AND EXISTS (
                            SELECT 1 FROM class_membership reviewer
                            WHERE reviewer.class_id=cm.class_id AND reviewer.user_id=:uid
                              AND reviewer.role='CLASS_COMMITTEE'
                        )
                    )
                )
                """).param("id", id).param("uid", userId)
                .query((rs, n) -> new StoredPdf(rs.getString(1), rs.getString(2), rs.getLong(3)))
                .optional().orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "PDF not found"));
        try {
            return new Download(pdf.filename(), pdf.size(), storage.open(pdf.key()));
        } catch (IOException e) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "PDF not found", e);
        }
    }

    private long userId(Principal principal) {
        return jdbc.sql("SELECT id FROM app_user WHERE username=:name AND enabled=TRUE")
                .param("name", principal.getName()).query(Long.class).single();
    }

    private DeclarationView owned(long id, long userId) {
        return queryOwned(" AND d.id=:id", userId, id, false).stream().findFirst()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Declaration not found"));
    }

    private List<DeclarationView> queryOwned(String suffix, long userId, Long id, boolean readable) {
        var spec = jdbc.sql("""
                SELECT d.id,cg.id class_id,cg.name class_name,d.title,d.status,d.created_at,d.updated_at,
                       p.original_filename,p.size_bytes,p.sha256,p.document_version,
                       d.submission_version,r.reason_code,r.custom_reason
                FROM declaration d JOIN class_membership cm ON cm.id=d.class_membership_id
                JOIN class_group cg ON cg.id=cm.class_id LEFT JOIN declaration_pdf p ON p.declaration_id=d.id
                LEFT JOIN review_record r ON r.declaration_id=d.id
                    AND r.submission_version=d.submission_version AND r.result='REJECTED'
                WHERE (cm.user_id=:uid OR (:readable=TRUE AND d.status<>'DRAFT' AND EXISTS (
                    SELECT 1 FROM class_membership reviewer
                    WHERE reviewer.class_id=cm.class_id AND reviewer.user_id=:uid
                      AND reviewer.role='CLASS_COMMITTEE')))
                """ + suffix).param("uid", userId).param("readable", readable);
        if (id != null) spec.param("id", id);
        return spec.query((rs, n) -> {
            String name = rs.getString("original_filename");
            PdfInfo pdf = name == null ? null : new PdfInfo(name, rs.getLong("size_bytes"), rs.getString("sha256"), rs.getLong("document_version"));
            return new DeclarationView(rs.getLong("id"), rs.getLong("class_id"), rs.getString("class_name"),
                    rs.getString("title"), rs.getString("status"), pdf != null, pdf,
                    instant(rs.getTimestamp("created_at")), instant(rs.getTimestamp("updated_at")),
                    rs.getLong("submission_version"), rs.getString("reason_code"), rs.getString("custom_reason"));
        }).list();
    }

    private void lockOwnedDraft(long id, long userId) {
        boolean allowed = jdbc.sql("""
                SELECT d.id FROM declaration d
                JOIN class_membership cm ON cm.id=d.class_membership_id
                WHERE d.id=:id AND cm.user_id=:userId AND d.status='DRAFT' FOR UPDATE
                """).param("id", id).param("userId", userId).query(Long.class).optional().isPresent();
        if (!allowed) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Declaration not found");
    }
    private boolean pdfExists(long id) {
        return jdbc.sql("SELECT COUNT(*) FROM declaration_pdf WHERE declaration_id=:id")
                .param("id", id).query(Integer.class).single() > 0;
    }

    private byte[] validate(MultipartFile file) {
        if (file == null || file.isEmpty()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "PDF is required");
        if (!"application/pdf".equalsIgnoreCase(file.getContentType()))
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Only application/pdf is accepted");
        if (file.getSize() > maxBytes) throw new ResponseStatusException(HttpStatus.PAYLOAD_TOO_LARGE, "PDF is too large");
        try {
            byte[] bytes = file.getBytes();
            if (bytes.length < PDF_SIGNATURE.length) throw invalidPdf();
            for (int i=0; i<PDF_SIGNATURE.length; i++) if (bytes[i] != PDF_SIGNATURE[i]) throw invalidPdf();
            return bytes;
        } catch (IOException e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Could not read PDF", e);
        }
    }

    private ResponseStatusException invalidPdf() {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid PDF signature");
    }

    private String safeFilename(String value) {
        if (value == null || value.isBlank()) return "document.pdf";
        String safe = value.replace("\r", "").replace("\n", "").replace("\\", "_").replace("/", "_").trim();
        return safe.length() > 255 ? safe.substring(safe.length() - 255) : safe;
    }

    private String sha256(byte[] bytes) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch (Exception e) { throw new IllegalStateException(e); }
    }

    private void deleteIfTransactionRollsBack(String key) {
        if (!TransactionSynchronizationManager.isSynchronizationActive()) return;
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCompletion(int status) {
                if (status != TransactionSynchronization.STATUS_COMMITTED) deleteQuietly(key);
            }
        });
    }
    private void deleteAfterCommit(String key) {
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                deleteQuietly(key);
            }
        });
    }
    private void deleteQuietly(String key) {
        try { storage.delete(key); } catch (IOException ignored) { }
    }

    private Instant instant(Timestamp value) { return value.toInstant(); }

    public record CreateRequest(@NotNull Long classId, @NotBlank @Size(max=200) String title) {}
    public record PdfInfo(String originalFilename, long sizeBytes, String sha256, long documentVersion) {}
    public record DeclarationView(long id, long classId, String className, String title, String status,
            boolean hasPdf, PdfInfo pdf, Instant createdAt, Instant updatedAt,
            long submissionVersion, String latestReasonCode, String latestCustomReason) {}
    public record Download(String filename, long size, InputStream content) {}
    private record StoredPdf(String key, String filename, long size) {}
    private record ExistingPdf(long id, String key) {}
}

package edu.xjtu.evaluation.workflow;

import java.io.IOException;
import java.io.InputStream;
import java.security.Principal;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

import jakarta.validation.constraints.NotNull;

import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.web.server.ResponseStatusException;

import edu.xjtu.evaluation.storage.FileStorageService;

@Service
public class WorkflowService {
    private final JdbcClient jdbc;
    private final FileStorageService storage;

    public WorkflowService(JdbcClient jdbc, FileStorageService storage) {
        this.jdbc = jdbc;
        this.storage = storage;
    }

    @Transactional
    public State submit(Principal principal, long declarationId, long expectedDocumentVersion) {
        long userId = userId(principal);
        DeclarationRow declaration = lockDeclaration(declarationId);
        requireOwner(declaration, userId);
        requireStatus(declaration, "DRAFT");
        PdfRow pdf = jdbc.sql("""
                SELECT id,storage_key,original_filename,size_bytes,sha256,document_version
                FROM declaration_pdf WHERE declaration_id=:id
                """).param("id", declarationId)
                .query((rs, n) -> new PdfRow(rs.getLong("id"), rs.getString("storage_key"),
                        rs.getString("original_filename"), rs.getLong("size_bytes"),
                        rs.getString("sha256"), rs.getLong("document_version")))
                .optional().orElseThrow(() -> businessError("提交前必须上传 PDF"));
        if (pdf.documentVersion() != expectedDocumentVersion) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "PDF 已变更，请刷新申报后再提交");
        }
        List<String> types = jdbc.sql("""
                SELECT DISTINCT type FROM evidence_region WHERE document_id=:documentId
                """).param("documentId", pdf.id()).query(String.class).list();
        if (!types.contains("IDENTITY")) throw businessError("提交前必须标注身份信息证据");
        if (!types.contains("VALIDITY")) throw businessError("提交前必须标注材料有效性证据");

        long nextVersion = declaration.submissionVersion() + 1;
        String snapshotKey = "submissions/" + declarationId + "/" + nextVersion + "/" + UUID.randomUUID() + ".pdf";
        try (InputStream source = storage.open(pdf.storageKey())) {
            storage.store(snapshotKey, source);
            deleteIfTransactionRollsBack(snapshotKey);
        } catch (IOException exception) {
            deleteQuietly(snapshotKey);
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "无法保存提交快照", exception);
        }

        GeneratedKeyHolder key = new GeneratedKeyHolder();
        jdbc.sql("""
                INSERT INTO declaration_submission
                    (declaration_id,submission_version,title,pdf_storage_key,pdf_original_filename,
                     pdf_size_bytes,pdf_sha256,pdf_document_version)
                VALUES(:id,:version,:title,:storageKey,:filename,:size,:sha,:documentVersion)
                """).param("id", declarationId).param("version", nextVersion)
                .param("title", declaration.title()).param("storageKey", snapshotKey)
                .param("filename", pdf.filename()).param("size", pdf.sizeBytes())
                .param("sha", pdf.sha256()).param("documentVersion", pdf.documentVersion())
                .update(key, "id");
        long submissionId = key.getKey().longValue();
        jdbc.sql("""
                INSERT INTO submission_evidence_region
                    (submission_id,type,page_number,x,y,width,height,source)
                SELECT :submissionId,type,page_number,x,y,width,height,source
                FROM evidence_region WHERE document_id=:documentId
                """).param("submissionId", submissionId).param("documentId", pdf.id()).update();
        jdbc.sql("""
                UPDATE declaration SET status='PENDING',submission_version=:version,
                    updated_at=CURRENT_TIMESTAMP WHERE id=:id
                """).param("version", nextVersion).param("id", declarationId).update();
        return new State(declarationId, "PENDING", nextVersion, pdf.documentVersion());
    }

    @Transactional
    public State reopen(Principal principal, long declarationId, long expectedSubmissionVersion) {
        long userId = userId(principal);
        DeclarationRow declaration = lockDeclaration(declarationId);
        requireOwner(declaration, userId);
        requireStatus(declaration, "REJECTED");
        if (declaration.submissionVersion() != expectedSubmissionVersion) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "申报版本已变化，请刷新");
        }
        jdbc.sql("UPDATE declaration SET status='DRAFT',updated_at=CURRENT_TIMESTAMP WHERE id=:id")
                .param("id", declarationId).update();
        long documentVersion = jdbc.sql(
                "SELECT document_version FROM declaration_pdf WHERE declaration_id=:id")
                .param("id", declarationId).query(Long.class).single();
        return new State(declarationId, "DRAFT", declaration.submissionVersion(), documentVersion);
    }

    @Transactional
    public State decide(Principal principal, long declarationId, DecisionRequest request) {
        long reviewerId = userId(principal);
        DeclarationRow declaration = lockDeclaration(declarationId);
        requireCommittee(declaration.classId(), reviewerId);
        if (declaration.ownerId() == reviewerId) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "不能审核自己的申报");
        }
        requireStatus(declaration, "PENDING");
        if (request.submissionVersion() == null
                || request.submissionVersion() != declaration.submissionVersion()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "提交版本已变化，请刷新待审核列表");
        }
        validateDecision(request);
        String customReason = request.customReason() == null ? null : request.customReason().trim();
        jdbc.sql("""
                INSERT INTO review_record
                    (declaration_id,reviewer_user_id,submission_version,result,reason_code,custom_reason)
                VALUES(:id,:reviewerId,:version,:result,:reasonCode,:customReason)
                """).param("id", declarationId).param("reviewerId", reviewerId)
                .param("version", declaration.submissionVersion())
                .param("result", request.result().name())
                .param("reasonCode", request.reasonCode() == null ? null : request.reasonCode().name())
                .param("customReason", customReason).update();
        jdbc.sql("UPDATE declaration SET status=:status,updated_at=CURRENT_TIMESTAMP WHERE id=:id")
                .param("status", request.result().name()).param("id", declarationId).update();
        long documentVersion = jdbc.sql(
                "SELECT document_version FROM declaration_pdf WHERE declaration_id=:id")
                .param("id", declarationId).query(Long.class).single();
        return new State(declarationId, request.result().name(),
                declaration.submissionVersion(), documentVersion);
    }

    @Transactional(readOnly = true)
    public List<QueueItem> queue(Principal principal, long classId) {
        long reviewerId = userId(principal);
        requireCommittee(classId, reviewerId);
        return jdbc.sql("""
                SELECT d.id,d.title,d.submission_version,d.updated_at,
                       u.id owner_id,u.username,u.display_name,cg.id class_id,cg.name class_name
                FROM declaration d
                JOIN class_membership cm ON cm.id=d.class_membership_id
                JOIN app_user u ON u.id=cm.user_id
                JOIN class_group cg ON cg.id=cm.class_id
                WHERE cm.class_id=:classId AND d.status='PENDING' AND u.id<>:reviewerId
                ORDER BY d.updated_at,d.id
                """).param("classId", classId).param("reviewerId", reviewerId)
                .query((rs, n) -> new QueueItem(rs.getLong("id"), rs.getString("title"),
                        rs.getLong("submission_version"), rs.getLong("owner_id"),
                        rs.getString("username"), rs.getString("display_name"),
                        rs.getLong("class_id"), rs.getString("class_name"),
                        rs.getTimestamp("updated_at").toInstant())).list();
    }

    @Transactional(readOnly = true)
    public List<ReviewEntry> history(Principal principal, long declarationId) {
        long userId = userId(principal);
        DeclarationRow declaration = readDeclaration(declarationId);
        if (declaration.ownerId() != userId) {
            if ("DRAFT".equals(declaration.status())) {
                throw new ResponseStatusException(HttpStatus.NOT_FOUND, "申报不存在");
            }
            requireCommittee(declaration.classId(), userId);
        }
        return jdbc.sql("""
                SELECT r.id,r.declaration_id,r.reviewer_user_id,r.submission_version,
                       r.result,r.reason_code,r.custom_reason,r.reviewed_at,u.display_name reviewer_name
                FROM review_record r JOIN app_user u ON u.id=r.reviewer_user_id
                WHERE r.declaration_id=:id ORDER BY r.submission_version DESC
                """).param("id", declarationId)
                .query((rs, n) -> new ReviewEntry(rs.getLong("id"), rs.getLong("declaration_id"),
                        rs.getLong("reviewer_user_id"), rs.getLong("submission_version"),
                        Result.valueOf(rs.getString("result")),
                        rs.getString("reason_code") == null ? null : ReasonCode.valueOf(rs.getString("reason_code")),
                        rs.getString("custom_reason"), rs.getString("reviewer_name"),
                        rs.getTimestamp("reviewed_at").toInstant())).list();
    }

    private DeclarationRow lockDeclaration(long id) {
        return queryDeclaration(id, true);
    }

    private DeclarationRow readDeclaration(long id) {
        return queryDeclaration(id, false);
    }

    private DeclarationRow queryDeclaration(long id, boolean lock) {
        return jdbc.sql("""
                SELECT d.id,d.title,d.status,d.submission_version,cm.class_id,cm.user_id
                FROM declaration d JOIN class_membership cm ON cm.id=d.class_membership_id
                WHERE d.id=:id
                """ + (lock ? " FOR UPDATE" : "")).param("id", id)
                .query((rs, n) -> new DeclarationRow(rs.getLong("id"), rs.getString("title"),
                        rs.getString("status"), rs.getLong("submission_version"),
                        rs.getLong("class_id"), rs.getLong("user_id")))
                .optional().orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "申报不存在"));
    }

    private void requireOwner(DeclarationRow declaration, long userId) {
        if (declaration.ownerId() != userId) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "申报不存在");
        }
    }

    private void requireCommittee(long classId, long userId) {
        boolean authorized = jdbc.sql("""
                SELECT id FROM class_membership
                WHERE class_id=:classId AND user_id=:userId AND role='CLASS_COMMITTEE'
                """).param("classId", classId).param("userId", userId)
                .query(Long.class).optional().isPresent();
        if (!authorized) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "申报不存在");
    }

    private void requireStatus(DeclarationRow declaration, String status) {
        if (!status.equals(declaration.status())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "申报状态已变化，请刷新");
        }
    }

    private void validateDecision(DecisionRequest request) {
        if (request == null || request.result() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "审核结果必填");
        }
        if (request.result() == Result.APPROVED) {
            if (request.reasonCode() != null || request.customReason() != null) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "通过审核不能填写驳回原因");
            }
            return;
        }
        if (request.reasonCode() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "驳回原因必填");
        }
        if (request.reasonCode() == ReasonCode.OTHER) {
            if (request.customReason() == null || request.customReason().isBlank()) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "其他原因必须填写说明");
            }
            if (request.customReason().trim().length() > 500) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "说明不能超过500字");
            }
        } else if (request.customReason() != null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "此驳回原因不能附加自定义说明");
        }
    }

    private long userId(Principal principal) {
        return jdbc.sql("SELECT id FROM app_user WHERE username=:username AND enabled=TRUE")
                .param("username", principal.getName()).query(Long.class).single();
    }

    private ResponseStatusException businessError(String message) {
        return new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, message);
    }

    private void deleteIfTransactionRollsBack(String key) {
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCompletion(int status) {
                if (status != TransactionSynchronization.STATUS_COMMITTED) deleteQuietly(key);
            }
        });
    }

    private void deleteQuietly(String key) {
        try {
            storage.delete(key);
        } catch (IOException ignored) {
            // Failed orphan cleanup can be retried by an operational job.
        }
    }

    public enum Result { APPROVED, REJECTED }
    public enum ReasonCode { ACTIVITY_INVALID, EVIDENCE_INVALID, IDENTITY_NOT_FOUND, OTHER }
    public record DecisionRequest(@NotNull Long submissionVersion, @NotNull Result result,
            ReasonCode reasonCode, String customReason) {}
    public record State(long id, String status, long submissionVersion, long documentVersion) {}
    public record QueueItem(long id, String title, long submissionVersion, long ownerId,
            String ownerUsername, String ownerDisplayName, long classId, String className, Instant updatedAt) {}
    public record ReviewEntry(long id, long declarationId, long reviewerUserId, long submissionVersion,
            Result result, ReasonCode reasonCode, String customReason, String reviewerName, Instant reviewedAt) {}
    private record DeclarationRow(long id, String title, String status, long submissionVersion,
            long classId, long ownerId) {}
    private record PdfRow(long id, String storageKey, String filename, long sizeBytes,
            String sha256, long documentVersion) {}
}

package edu.xjtu.evaluation.pdftext;

import java.security.Principal;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;
import edu.xjtu.evaluation.evidence.EvidenceRegionService;
import edu.xjtu.evaluation.evidence.EvidenceRegionService.Region;
import edu.xjtu.evaluation.pdftext.IdentityLocator.Candidate;

@Service
public class IdentityCandidateService {
    private final JdbcClient jdbc;
    private final PdfAnalysisCache cache;
    private final IdentityLocator locator;
    private final EvidenceRegionService evidence;
    public IdentityCandidateService(JdbcClient jdbc,PdfAnalysisCache cache,IdentityLocator locator,
            EvidenceRegionService evidence) {
        this.jdbc=jdbc;this.cache=cache;this.locator=locator;this.evidence=evidence;
    }
    @Transactional(readOnly = true)
    public CandidateResult list(Principal principal,long declarationId) {
        Document document = document(principal,declarationId,false);
        var analysis = cache.current(document.id(),document.version(),document.storageKey());
        List<Candidate> candidates = analysis.status() == PdfTextAnalyzer.Status.TEXT_AVAILABLE
                ? locator.locate(document.studentNumber(),document.studentName(),analysis.blocks()) : List.of();
        return new CandidateResult(document.version(),document.pageCount(),analysis.status(),candidates);
    }
    @Transactional
    public Region confirm(Principal principal,long declarationId,long expectedVersion,String candidateId) {
        Document document = document(principal,declarationId,true);
        if (expectedVersion != document.version())
            throw new ResponseStatusException(HttpStatus.CONFLICT,"PDF 已替换，请重新查看候选区域");
        var analysis = cache.current(document.id(),document.version(),document.storageKey());
        if (analysis.status() != PdfTextAnalyzer.Status.TEXT_AVAILABLE)
            throw new ResponseStatusException(HttpStatus.CONFLICT,"当前 PDF 没有可确认的文本候选");
        Candidate selected = locator.locate(document.studentNumber(),document.studentName(),analysis.blocks())
                .stream().filter(candidate -> candidate.id().equals(candidateId)).findFirst()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.CONFLICT,"候选区域已变化，请重新查看"));
        // The client supplies only an ID. Geometry and source come from the current server analysis.
        return evidence.confirmCandidate(principal,declarationId,expectedVersion,selected);
    }
    private Document document(Principal principal,long declarationId,boolean lock) {
        String sql="""
                SELECT p.id,p.storage_key,p.document_version,p.page_count,
                       u.student_number,u.student_name
                FROM declaration d JOIN class_membership cm ON cm.id=d.class_membership_id
                JOIN app_user u ON u.id=cm.user_id
                JOIN declaration_pdf p ON p.declaration_id=d.id
                WHERE d.id=:id AND d.status='DRAFT' AND u.username=:username AND u.enabled=TRUE
                """+(lock ? " FOR UPDATE" : "");
        return jdbc.sql(sql).param("id",declarationId).param("username",principal.getName())
                .query((rs,n)->new Document(rs.getLong("id"),rs.getString("storage_key"),
                        rs.getLong("document_version"),(Integer)rs.getObject("page_count"),
                        rs.getString("student_number"),rs.getString("student_name")))
                .optional().orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND,"草稿 PDF 不存在"));
    }
    public record CandidateResult(long documentVersion,Integer pageCount,PdfTextAnalyzer.Status analysisStatus,
            List<Candidate> candidates) {}
    private record Document(long id,String storageKey,long version,Integer pageCount,
            String studentNumber,String studentName) {}
}

package edu.xjtu.evaluation.pdftext;

import java.security.Principal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import edu.xjtu.evaluation.evidence.EvidenceRegionService.Region;
import edu.xjtu.evaluation.pdftext.IdentityCandidateService.CandidateResult;

@RestController
@RequestMapping("/api/declarations/{id}/identity-candidates")
public class IdentityCandidateController {
    private final IdentityCandidateService service;
    public IdentityCandidateController(IdentityCandidateService service) {this.service=service;}
    @GetMapping
    public CandidateResult list(Principal principal,@PathVariable long id) {
        return service.list(principal,id);
    }
    @PostMapping("/{candidateId}/confirm")
    public Region confirm(Principal principal,@PathVariable long id,@PathVariable String candidateId,
            @RequestHeader("If-Match") long documentVersion) {
        return service.confirm(principal,id,documentVersion,candidateId);
    }
}

package edu.xjtu.evaluation.evidence;

import java.security.Principal;
import java.util.List;

import jakarta.validation.Valid;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import edu.xjtu.evaluation.evidence.EvidenceRegionService.Region;
import edu.xjtu.evaluation.evidence.EvidenceRegionService.RegionInput;

@RestController
@RequestMapping("/api/declarations/{declarationId}/evidence-regions")
public class EvidenceRegionController {
    private final EvidenceRegionService service;

    public EvidenceRegionController(EvidenceRegionService service) {
        this.service = service;
    }

    @GetMapping
    public List<Region> list(Principal principal, @PathVariable long declarationId) {
        return service.list(principal, declarationId);
    }

    @PostMapping
    public ResponseEntity<Region> create(Principal principal, @PathVariable long declarationId,
            @RequestHeader("If-Match") long documentVersion, @Valid @RequestBody RegionInput input) {
        return ResponseEntity.status(201)
                .body(service.create(principal, declarationId, documentVersion, input));
    }

    @PutMapping("/{regionId}")
    public Region update(Principal principal, @PathVariable long declarationId, @PathVariable long regionId,
            @RequestHeader("If-Match") long documentVersion, @Valid @RequestBody RegionInput input) {
        return service.update(principal, declarationId, regionId, documentVersion, input);
    }

    @DeleteMapping("/{regionId}")
    public ResponseEntity<Void> delete(Principal principal, @PathVariable long declarationId,
            @PathVariable long regionId, @RequestHeader("If-Match") long documentVersion) {
        service.delete(principal, declarationId, regionId, documentVersion);
        return ResponseEntity.noContent().build();
    }
}

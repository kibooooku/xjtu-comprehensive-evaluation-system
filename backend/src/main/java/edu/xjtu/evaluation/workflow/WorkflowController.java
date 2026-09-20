package edu.xjtu.evaluation.workflow;

import java.security.Principal;
import java.util.List;

import jakarta.validation.Valid;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import edu.xjtu.evaluation.workflow.WorkflowService.DecisionRequest;
import edu.xjtu.evaluation.workflow.WorkflowService.QueueItem;
import edu.xjtu.evaluation.workflow.WorkflowService.ReviewEntry;
import edu.xjtu.evaluation.workflow.WorkflowService.State;

@RestController
@RequestMapping("/api")
public class WorkflowController {
    private final WorkflowService service;

    public WorkflowController(WorkflowService service) {
        this.service = service;
    }

    @PostMapping("/declarations/{id}/submit")
    public State submit(Principal principal, @PathVariable long id,
            @RequestHeader("If-Match") long documentVersion) {
        return service.submit(principal, id, documentVersion);
    }

    @PostMapping("/declarations/{id}/revise")
    public State revise(Principal principal, @PathVariable long id,
            @RequestHeader("If-Match") long submissionVersion) {
        return service.reopen(principal, id, submissionVersion);
    }

    @GetMapping("/declarations/{id}/reviews")
    public List<ReviewEntry> history(Principal principal, @PathVariable long id) {
        return service.history(principal, id);
    }

    @GetMapping("/review/classes/{classId}/pending")
    public List<QueueItem> queue(Principal principal, @PathVariable long classId) {
        return service.queue(principal, classId);
    }

    @PostMapping("/review/declarations/{id}/decision")
    public State decide(Principal principal, @PathVariable long id,
            @Valid @RequestBody DecisionRequest request) {
        return service.decide(principal, id, request);
    }
}

package edu.xjtu.evaluation.score;

import java.security.Principal;
import java.util.List;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import edu.xjtu.evaluation.score.ScoreService.Input;
import edu.xjtu.evaluation.score.ScoreService.Item;
import edu.xjtu.evaluation.score.ScoreService.Rule;

@RestController
@RequestMapping("/api")
public class ScoreController {
    private final ScoreService service;
    public ScoreController(ScoreService service) {this.service=service;}

    @GetMapping("/score-rules")
    public List<Rule> rules(Principal principal,@RequestParam long classId) {return service.rules(principal,classId);}

    @GetMapping("/declarations/{id}/score-items")
    public List<Item> list(Principal principal,@PathVariable long id) {return service.list(principal,id);}

    @PostMapping("/declarations/{id}/score-items")
    public ResponseEntity<Item> create(Principal principal,@PathVariable long id,
            @RequestHeader("If-Match") long submissionVersion,@Valid @RequestBody Input input) {
        return ResponseEntity.status(201).body(service.create(principal,id,submissionVersion,input));
    }

    @PutMapping("/declarations/{id}/score-items/{itemId}")
    public Item update(Principal principal,@PathVariable long id,@PathVariable long itemId,
            @RequestHeader("If-Match") long submissionVersion,@Valid @RequestBody Input input) {
        return service.update(principal,id,itemId,submissionVersion,input);
    }

    @DeleteMapping("/declarations/{id}/score-items/{itemId}")
    public ResponseEntity<Void> delete(Principal principal,@PathVariable long id,@PathVariable long itemId,
            @RequestHeader("If-Match") long submissionVersion) {
        service.delete(principal,id,itemId,submissionVersion);
        return ResponseEntity.noContent().build();
    }
}

package edu.xjtu.evaluation.declaration;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.Principal;
import java.util.List;

import jakarta.validation.Valid;

import org.springframework.core.io.InputStreamResource;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import edu.xjtu.evaluation.declaration.DeclarationService.CreateRequest;
import edu.xjtu.evaluation.declaration.DeclarationService.DeclarationView;
import edu.xjtu.evaluation.declaration.DeclarationService.Download;

@RestController
@RequestMapping("/api/declarations")
public class DeclarationController {
    private final DeclarationService service;

    public DeclarationController(DeclarationService service) { this.service = service; }

    @PostMapping
    ResponseEntity<DeclarationView> create(Principal principal, @Valid @RequestBody CreateRequest request) {
        return ResponseEntity.status(201).body(service.create(principal, request));
    }

    @GetMapping("/mine")
    List<DeclarationView> mine(Principal principal) { return service.mine(principal); }

    @GetMapping("/{id}")
    DeclarationView get(Principal principal, @PathVariable long id) { return service.get(principal, id); }

    @PostMapping(path="/{id}/pdf", consumes=MediaType.MULTIPART_FORM_DATA_VALUE)
    ResponseEntity<DeclarationView> upload(Principal principal, @PathVariable long id,
            @RequestPart("file") MultipartFile file) {
        return ResponseEntity.status(201).body(service.upload(principal, id, file));
    }

    @PutMapping(path="/{id}/pdf", consumes=MediaType.MULTIPART_FORM_DATA_VALUE)
    ResponseEntity<DeclarationView> replacePdf(Principal principal, @PathVariable long id,
            @RequestPart("file") MultipartFile file) {
        return ResponseEntity.ok(service.replacePdf(principal, id, file));
    }
    @GetMapping(path="/{id}/pdf", produces=MediaType.APPLICATION_PDF_VALUE)
    ResponseEntity<InputStreamResource> pdf(Principal principal, @PathVariable long id) {
        Download pdf = service.download(principal, id);
        String name = URLEncoder.encode(pdf.filename(), StandardCharsets.UTF_8).replace("+", "%20");
        return ResponseEntity.ok().contentType(MediaType.APPLICATION_PDF).contentLength(pdf.size())
                .cacheControl(CacheControl.noStore().cachePrivate())
                .header(HttpHeaders.CONTENT_DISPOSITION, "inline; filename=\"document.pdf\"; filename*=UTF-8''" + name)
                .body(new InputStreamResource(pdf.content()));
    }
}

package edu.xjtu.evaluation.storage;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class LocalFileStorageService implements FileStorageService {

    private final Path storageRoot;

    public LocalFileStorageService(@Value("${app.storage.root}") Path storageRoot) {
        this.storageRoot = storageRoot.toAbsolutePath().normalize();
    }

    @Override
    public String store(String storageKey, InputStream content) throws IOException {
        Path destination = resolveSafely(storageKey);
        Files.createDirectories(destination.getParent());
        Files.copy(content, destination, StandardCopyOption.REPLACE_EXISTING);
        return storageKey;
    }

    @Override
    public InputStream open(String storageKey) throws IOException {
        return Files.newInputStream(resolveSafely(storageKey));
    }

    private Path resolveSafely(String storageKey) {
        Path resolved = storageRoot.resolve(storageKey).normalize();
        if (!resolved.startsWith(storageRoot)) {
            throw new IllegalArgumentException("Storage key escapes the configured storage root");
        }
        return resolved;
    }
}

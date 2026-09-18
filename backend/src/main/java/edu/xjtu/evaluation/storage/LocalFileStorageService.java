package edu.xjtu.evaluation.storage;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;

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
        Files.copy(content, destination);
        return storageKey;
    }

    @Override
    public InputStream open(String storageKey) throws IOException {
        return Files.newInputStream(resolveSafely(storageKey));
    }

    @Override
    public void delete(String storageKey) throws IOException {
        Files.deleteIfExists(resolveSafely(storageKey));
    }

    private Path resolveSafely(String storageKey) {
        if (storageKey == null || storageKey.isBlank()) throw new IllegalArgumentException("Storage key must not be blank");
        Path resolved = storageRoot.resolve(storageKey).normalize();
        if (!resolved.startsWith(storageRoot)) throw new IllegalArgumentException("Storage key escapes storage root");
        return resolved;
    }
}

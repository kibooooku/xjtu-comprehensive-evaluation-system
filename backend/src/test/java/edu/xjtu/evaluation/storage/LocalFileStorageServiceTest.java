package edu.xjtu.evaluation.storage;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class LocalFileStorageServiceTest {
    @TempDir Path storageRoot;

    @Test
    void storesAndReadsFileWithinConfiguredRoot() throws Exception {
        var storage = new LocalFileStorageService(storageRoot);
        byte[] content = "%PDF-1.4\nfictional\n".getBytes(StandardCharsets.UTF_8);

        storage.store("declarations/42/material.pdf", new ByteArrayInputStream(content));

        assertThat(storage.open("declarations/42/material.pdf").readAllBytes()).isEqualTo(content);
        assertThat(Files.readAllBytes(storageRoot.resolve("declarations/42/material.pdf"))).isEqualTo(content);
    }

    @Test
    void rejectsPathTraversalForEveryOperation() {
        var storage = new LocalFileStorageService(storageRoot);

        assertThatThrownBy(() -> storage.store("../outside.pdf", new ByteArrayInputStream(new byte[0])))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> storage.open("../outside.pdf"))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> storage.delete("../outside.pdf"))
                .isInstanceOf(IllegalArgumentException.class);
        assertThat(storageRoot.resolveSibling("outside.pdf")).doesNotExist();
    }
}

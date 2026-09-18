package edu.xjtu.evaluation.storage;

import java.io.IOException;
import java.io.InputStream;

public interface FileStorageService {

    String store(String storageKey, InputStream content) throws IOException;

    InputStream open(String storageKey) throws IOException;
}

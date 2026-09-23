package edu.xjtu.evaluation.pdftext;

import java.io.IOException;
import java.io.InputStream;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import edu.xjtu.evaluation.storage.FileStorageService;

@Service
public class PdfAnalysisCache {
    private final PdfTextAnalyzer analyzer;
    private final FileStorageService storage;
    private final Map<Key,PdfTextAnalyzer.Analysis> cache = new LinkedHashMap<>(128,0.75f,true) {
        @Override protected boolean removeEldestEntry(Map.Entry<Key,PdfTextAnalyzer.Analysis> eldest) {
            return size() > 8;
        }
    };
    public PdfAnalysisCache(PdfTextAnalyzer analyzer,FileStorageService storage) {
        this.analyzer=analyzer;
        this.storage=storage;
    }
    public PdfTextAnalyzer.Analysis analyze(byte[] bytes) {return analyzer.analyze(bytes);}

    public PdfTextAnalyzer.Analysis current(long documentId,long documentVersion,String key) {
        Key identity = new Key(documentId,documentVersion);
        synchronized (this) {
            PdfTextAnalyzer.Analysis existing = cache.get(identity);
            if (existing != null) return existing;
        }
        // Parsing a PDF can take time; do not hold the shared cache lock during IO/analysis.
        try (InputStream input = storage.open(key)) {
            PdfTextAnalyzer.Analysis result = analyzer.analyze(input.readAllBytes());
            synchronized (this) {
                PdfTextAnalyzer.Analysis existing = cache.get(identity);
                if (existing != null) return existing;
                cache.put(identity,result);
            }
            return result;
        } catch (IOException failure) {
            return new PdfTextAnalyzer.Analysis(null,PdfTextAnalyzer.Status.FAILED,java.util.List.of());
        }
    }
    public void storeAfterCommit(long documentId,long newVersion,PdfTextAnalyzer.Analysis result,Long oldVersion) {
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override public void afterCommit() {
                synchronized (PdfAnalysisCache.this) {
                    if(oldVersion!=null) cache.remove(new Key(documentId,oldVersion));
                    cache.put(new Key(documentId,newVersion),result);
                }
            }
        });
    }
    private record Key(long documentId,long documentVersion) {}
}

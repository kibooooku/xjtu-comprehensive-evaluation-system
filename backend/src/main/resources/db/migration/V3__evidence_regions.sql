ALTER TABLE declaration_pdf ADD COLUMN document_version BIGINT NOT NULL DEFAULT 1;

CREATE TABLE evidence_region (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    document_id BIGINT NOT NULL,
    type VARCHAR(32) NOT NULL,
    page_number INT NOT NULL,
    x DECIMAL(9,6) NOT NULL,
    y DECIMAL(9,6) NOT NULL,
    width DECIMAL(9,6) NOT NULL,
    height DECIMAL(9,6) NOT NULL,
    source VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_evidence_document FOREIGN KEY (document_id) REFERENCES declaration_pdf (id),
    CONSTRAINT ck_evidence_type CHECK (type IN ('IDENTITY', 'VALIDITY')),
    CONSTRAINT ck_evidence_source CHECK (source IN ('MANUAL', 'OCR')),
    CONSTRAINT ck_evidence_page CHECK (page_number > 0),
    CONSTRAINT ck_evidence_x CHECK (x >= 0 AND x < 1),
    CONSTRAINT ck_evidence_y CHECK (y >= 0 AND y < 1),
    CONSTRAINT ck_evidence_width CHECK (width > 0 AND width <= 1),
    CONSTRAINT ck_evidence_height CHECK (height > 0 AND height <= 1),
    CONSTRAINT ck_evidence_right CHECK (x + width <= 1),
    CONSTRAINT ck_evidence_bottom CHECK (y + height <= 1)
);
CREATE INDEX idx_evidence_document_page ON evidence_region (document_id, page_number);

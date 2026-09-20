ALTER TABLE declaration DROP CONSTRAINT ck_declaration_status;
ALTER TABLE declaration ADD CONSTRAINT ck_declaration_status
    CHECK (status IN ('DRAFT', 'PENDING', 'APPROVED', 'REJECTED'));
ALTER TABLE declaration ADD COLUMN submission_version BIGINT NOT NULL DEFAULT 0;

CREATE TABLE declaration_submission (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    declaration_id BIGINT NOT NULL,
    submission_version BIGINT NOT NULL,
    title VARCHAR(200) NOT NULL,
    pdf_storage_key VARCHAR(500) NOT NULL UNIQUE,
    pdf_original_filename VARCHAR(255) NOT NULL,
    pdf_size_bytes BIGINT NOT NULL,
    pdf_sha256 VARCHAR(64) NOT NULL,
    pdf_document_version BIGINT NOT NULL,
    submitted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_submission_declaration FOREIGN KEY (declaration_id) REFERENCES declaration (id),
    CONSTRAINT uk_submission_version UNIQUE (declaration_id, submission_version),
    CONSTRAINT ck_submission_version CHECK (submission_version > 0)
);
CREATE INDEX idx_submission_declaration ON declaration_submission (declaration_id);

CREATE TABLE submission_evidence_region (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    submission_id BIGINT NOT NULL,
    type VARCHAR(32) NOT NULL,
    page_number INT NOT NULL,
    x DECIMAL(9,6) NOT NULL,
    y DECIMAL(9,6) NOT NULL,
    width DECIMAL(9,6) NOT NULL,
    height DECIMAL(9,6) NOT NULL,
    source VARCHAR(32) NOT NULL,
    CONSTRAINT fk_submission_region FOREIGN KEY (submission_id) REFERENCES declaration_submission (id),
    CONSTRAINT ck_submission_region_type CHECK (type IN ('IDENTITY', 'VALIDITY')),
    CONSTRAINT ck_submission_region_source CHECK (source IN ('MANUAL', 'OCR'))
);
CREATE INDEX idx_submission_region_submission ON submission_evidence_region (submission_id);

CREATE TABLE review_record (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    declaration_id BIGINT NOT NULL,
    reviewer_user_id BIGINT NOT NULL,
    submission_version BIGINT NOT NULL,
    result VARCHAR(32) NOT NULL,
    reason_code VARCHAR(32),
    custom_reason VARCHAR(500),
    reviewed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_review_declaration FOREIGN KEY (declaration_id) REFERENCES declaration (id),
    CONSTRAINT fk_review_reviewer FOREIGN KEY (reviewer_user_id) REFERENCES app_user (id),
    CONSTRAINT fk_review_submission FOREIGN KEY (declaration_id, submission_version)
        REFERENCES declaration_submission (declaration_id, submission_version),
    CONSTRAINT uk_review_submission UNIQUE (declaration_id, submission_version),
    CONSTRAINT ck_review_result CHECK (result IN ('APPROVED', 'REJECTED')),
    CONSTRAINT ck_review_reason CHECK (
        (result = 'APPROVED' AND reason_code IS NULL AND custom_reason IS NULL)
        OR (result = 'REJECTED' AND reason_code IN
            ('ACTIVITY_INVALID', 'EVIDENCE_INVALID', 'IDENTITY_NOT_FOUND', 'OTHER')
            AND ((reason_code = 'OTHER' AND custom_reason IS NOT NULL)
                OR (reason_code <> 'OTHER' AND custom_reason IS NULL)))
    )
);
CREATE INDEX idx_review_declaration ON review_record (declaration_id, reviewed_at);

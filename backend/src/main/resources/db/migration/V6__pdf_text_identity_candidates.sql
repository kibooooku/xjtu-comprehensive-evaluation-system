ALTER TABLE app_user ADD COLUMN student_number VARCHAR(32);
ALTER TABLE app_user ADD COLUMN student_name VARCHAR(100);
ALTER TABLE app_user ADD CONSTRAINT uk_app_user_student_number UNIQUE (student_number);

ALTER TABLE declaration_pdf ADD COLUMN page_count INT;
ALTER TABLE declaration_pdf ADD COLUMN text_analysis_status VARCHAR(20) NOT NULL DEFAULT 'FAILED';
ALTER TABLE declaration_pdf ADD CONSTRAINT ck_pdf_page_count CHECK (page_count IS NULL OR page_count > 0);
ALTER TABLE declaration_pdf ADD CONSTRAINT ck_pdf_text_status
 CHECK (text_analysis_status IN ('TEXT_AVAILABLE','NO_TEXT','FAILED'));

ALTER TABLE evidence_region DROP CONSTRAINT ck_evidence_source;
ALTER TABLE evidence_region ADD CONSTRAINT ck_evidence_source
 CHECK (source IN ('MANUAL','OCR','PDF_TEXT_AUTO'));
ALTER TABLE submission_evidence_region DROP CONSTRAINT ck_submission_region_source;
ALTER TABLE submission_evidence_region ADD CONSTRAINT ck_submission_region_source
 CHECK (source IN ('MANUAL','OCR','PDF_TEXT_AUTO'));

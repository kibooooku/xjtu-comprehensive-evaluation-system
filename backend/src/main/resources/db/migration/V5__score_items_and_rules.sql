-- Official XJTU undergraduate assessment rules, p. 6; college overrides unresolved.
CREATE TABLE score_rule_set (
 version VARCHAR(64) NOT NULL PRIMARY KEY,
 source_document VARCHAR(255) NOT NULL,
 source_locator VARCHAR(255) NOT NULL
);
INSERT INTO score_rule_set(version,source_document,source_locator) VALUES
 ('XJTU_SCHOOL_2018_V1','西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛');
ALTER TABLE class_group ADD COLUMN score_rule_set_version VARCHAR(64) NOT NULL DEFAULT 'XJTU_SCHOOL_2018_V1';
ALTER TABLE class_group ADD CONSTRAINT fk_class_score_rule_set
 FOREIGN KEY(score_rule_set_version) REFERENCES score_rule_set(version);
CREATE TABLE score_rule (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 rule_set_version VARCHAR(64) NOT NULL,
 category VARCHAR(64) NOT NULL,
 subcategory VARCHAR(64) NOT NULL,
 item_type VARCHAR(64) NOT NULL,
 level VARCHAR(64),
 award VARCHAR(32),
 score DECIMAL(6,2) NOT NULL,
 source_document VARCHAR(255) NOT NULL,
 source_locator VARCHAR(255) NOT NULL,
 active BOOLEAN NOT NULL DEFAULT TRUE,
 CONSTRAINT fk_score_rule_set FOREIGN KEY(rule_set_version) REFERENCES score_rule_set(version),
 CONSTRAINT uk_score_rule_match UNIQUE (rule_set_version,category,subcategory,item_type,level,award),
 CONSTRAINT uk_score_rule_identity UNIQUE (id,category,subcategory,item_type,level,award),
 CONSTRAINT ck_score_rule_score CHECK (score >= 0),
 CONSTRAINT ck_score_rule_competition CHECK (item_type <> 'DISCIPLINE_COMPETITION' OR (level IS NOT NULL AND award IS NOT NULL))
);
CREATE INDEX idx_score_rule_active ON score_rule (active,category,subcategory,item_type,level,award);
CREATE TABLE score_item (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 declaration_id BIGINT NOT NULL,
 activity_name VARCHAR(200) NOT NULL,
 category VARCHAR(64) NOT NULL,
 subcategory VARCHAR(64) NOT NULL,
 item_type VARCHAR(64) NOT NULL,
 level VARCHAR(64),
 award VARCHAR(32),
 project_key VARCHAR(120),
 rule_id BIGINT NOT NULL,
 calculated_score DECIMAL(6,2) NOT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT fk_score_item_declaration FOREIGN KEY (declaration_id) REFERENCES declaration(id),
 CONSTRAINT fk_score_item_rule FOREIGN KEY (rule_id) REFERENCES score_rule(id),
 CONSTRAINT fk_score_item_rule_fields FOREIGN KEY (rule_id,category,subcategory,item_type,level,award)
  REFERENCES score_rule(id,category,subcategory,item_type,level,award),
 CONSTRAINT ck_score_item_score CHECK (calculated_score >= 0),
 CONSTRAINT ck_score_item_competition CHECK (item_type <> 'DISCIPLINE_COMPETITION' OR (level IS NOT NULL AND award IS NOT NULL))
);
CREATE INDEX idx_score_item_declaration ON score_item(declaration_id);
CREATE TABLE submission_score_item (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 submission_id BIGINT NOT NULL,
 source_score_item_id BIGINT NOT NULL,
 activity_name VARCHAR(200) NOT NULL,
 category VARCHAR(64) NOT NULL,
 subcategory VARCHAR(64) NOT NULL,
 item_type VARCHAR(64) NOT NULL,
 level VARCHAR(64),
 award VARCHAR(32),
 project_key VARCHAR(120),
 rule_id BIGINT NOT NULL,
 rule_set_version VARCHAR(64) NOT NULL,
 rule_source_document VARCHAR(255) NOT NULL,
 rule_source_locator VARCHAR(255) NOT NULL,
 calculated_score DECIMAL(6,2) NOT NULL,
 CONSTRAINT fk_submission_score_submission FOREIGN KEY (submission_id) REFERENCES declaration_submission(id),
 CONSTRAINT fk_submission_score_rule FOREIGN KEY (rule_id) REFERENCES score_rule(id),
 CONSTRAINT uk_submission_score_source UNIQUE (submission_id,source_score_item_id),
 CONSTRAINT ck_submission_score_score CHECK (calculated_score >= 0),
 CONSTRAINT ck_submission_score_competition CHECK (item_type <> 'DISCIPLINE_COMPETITION' OR (level IS NOT NULL AND award IS NOT NULL))
);
CREATE INDEX idx_submission_score_submission ON submission_score_item(submission_id);
INSERT INTO score_rule (rule_set_version,category,subcategory,item_type,level,award,score,source_document,source_locator,active) VALUES
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','HIGH_LEVEL_INTERNATIONAL','SPECIAL',10.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','HIGH_LEVEL_INTERNATIONAL','FIRST',10.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','HIGH_LEVEL_INTERNATIONAL','SECOND',9.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','HIGH_LEVEL_INTERNATIONAL','THIRD',8.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','NATIONAL','SPECIAL',10.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','NATIONAL','FIRST',10.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','NATIONAL','SECOND',9.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','NATIONAL','THIRD',8.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','PROVINCIAL','SPECIAL',8.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','PROVINCIAL','FIRST',8.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','PROVINCIAL','SECOND',6.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','PROVINCIAL','THIRD',4.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','SCHOOL','SPECIAL',4.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','SCHOOL','FIRST',4.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','SCHOOL','SECOND',3.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','SCHOOL','THIRD',2.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','SCHOOL','EXCELLENCE',1.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','LOCAL_AUTHORITY','SPECIAL',4.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','LOCAL_AUTHORITY','FIRST',4.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','LOCAL_AUTHORITY','SECOND',3.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','LOCAL_AUTHORITY','THIRD',2.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','LOCAL_AUTHORITY','EXCELLENCE',1.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','INDUSTRY_ENTERPRISE','SPECIAL',4.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','INDUSTRY_ENTERPRISE','FIRST',4.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','INDUSTRY_ENTERPRISE','SECOND',3.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','INDUSTRY_ENTERPRISE','THIRD',2.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','INDUSTRY_ENTERPRISE','EXCELLENCE',1.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','SOCIETY_ASSOCIATION','SPECIAL',4.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','SOCIETY_ASSOCIATION','FIRST',4.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','SOCIETY_ASSOCIATION','SECOND',3.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','SOCIETY_ASSOCIATION','THIRD',2.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE),
('XJTU_SCHOOL_2018_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','SOCIETY_ASSOCIATION','EXCELLENCE',1.00,'西安交通大学本科生综合素质测评成绩评定办法','第6页：学术科研及创新创业·学科竞赛',TRUE);

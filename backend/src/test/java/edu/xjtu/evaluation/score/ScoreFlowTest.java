package edu.xjtu.evaluation.score;

import static org.assertj.core.api.Assertions.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.httpBasic;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import java.nio.charset.StandardCharsets;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import com.fasterxml.jackson.databind.ObjectMapper;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class ScoreFlowTest {
    @Autowired MockMvc mvc;
    @Autowired JdbcClient jdbc;
    @Autowired PasswordEncoder encoder;
    @Autowired ObjectMapper json;

    @BeforeEach void seed() {
        for (String table : new String[]{"review_record","submission_score_item","submission_evidence_region",
                "declaration_submission","score_item","evidence_region","declaration_pdf","declaration",
                "class_membership","class_group","app_user"}) jdbc.sql("DELETE FROM "+table).update();
        for (int i=1;i<=4;i++) jdbc.sql("INSERT INTO app_user(id,username,password_hash,display_name,enabled) VALUES(:id,:name,:hash,:name,TRUE)")
                .param("id",i).param("name",new String[]{"owner","classmate","committee","outside"}[i-1])
                .param("hash",encoder.encode("test-password")).update();
        jdbc.sql("INSERT INTO class_group(id,code,name) VALUES(10,'DEMO-01','虚构一班'),(20,'DEMO-02','虚构二班')").update();
        jdbc.sql("INSERT INTO class_membership(id,class_id,user_id,role) VALUES(101,10,1,'STUDENT'),(102,10,2,'STUDENT'),(103,10,3,'CLASS_COMMITTEE'),(104,20,4,'CLASS_COMMITTEE')").update();
    }

    @Test void rulesAreTraceableAndMatchRepresentativeLevelsAndSpecialAward() throws Exception {
        mvc.perform(get("/api/score-rules?classId=10").with(auth("owner")))
                .andExpect(status().isOk()).andExpect(jsonPath("$[0].ruleSetVersion").value("XJTU_SCHOOL_2018_V1"));
        mvc.perform(get("/api/score-rules?classId=20").with(auth("owner"))).andExpect(status().isNotFound());
        long id=draft();
        for (String[] rule : new String[][]{{"NATIONAL","FIRST","10.0"},{"NATIONAL","SECOND","9.0"},
                {"NATIONAL","THIRD","8.0"},{"PROVINCIAL","FIRST","8.0"},{"PROVINCIAL","SECOND","6.0"},
                {"PROVINCIAL","THIRD","4.0"},{"SCHOOL","FIRST","4.0"},{"SCHOOL","SECOND","3.0"},
                {"SCHOOL","THIRD","2.0"},{"SCHOOL","EXCELLENCE","1.0"},{"NATIONAL","SPECIAL","10.0"}}) {
            String response=mvc.perform(post(path(id)).with(auth("owner")).header("If-Match","0")
                    .contentType(MediaType.APPLICATION_JSON).content(body(rule[0],rule[1])))
                    .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
            assertThat(json.readTree(response).get("calculatedScore").decimalValue()).isEqualByComparingTo(rule[2]);
        }
        assertThat(jdbc.sql("SELECT COUNT(*) FROM score_item WHERE declaration_id=:id").param("id",id)
                .query(Integer.class).single()).isEqualTo(11);
    }

    @Test void invalidCombinationAndForgedScoreAreRejected() throws Exception {
        long id=draft();
        mvc.perform(post(path(id)).with(auth("owner")).header("If-Match","0")
                .contentType(MediaType.APPLICATION_JSON).content(body("NATIONAL","EXCELLENCE")))
                .andExpect(status().isUnprocessableEntity());
        mvc.perform(post(path(id)).with(auth("owner")).header("If-Match","0")
                .contentType(MediaType.APPLICATION_JSON).content(body("NATIONAL","FIRST").replace("\"award\"", "\"calculatedScore\":999,\"award\"")))
                .andExpect(status().isBadRequest());
        assertThat(jdbc.sql("SELECT COUNT(*) FROM score_item WHERE declaration_id=:id").param("id",id)
                .query(Integer.class).single()).isZero();
    }

    @Test void ownerCrudAndVersionGuardAndResourcePermissions() throws Exception {
        long id=draft();
        mvc.perform(get(path(id)).with(auth("classmate"))).andExpect(status().isNotFound());
        mvc.perform(get(path(id)).with(auth("committee"))).andExpect(status().isNotFound());
        mvc.perform(get(path(id)).with(auth("outside"))).andExpect(status().isNotFound());
        for (String user:new String[]{"classmate","committee","outside"})
            mvc.perform(post(path(id)).with(auth(user)).header("If-Match","0")
                    .contentType(MediaType.APPLICATION_JSON).content(body("NATIONAL","FIRST")))
                    .andExpect(status().isNotFound());
        long itemId=create(id,"NATIONAL","FIRST",0);
        mvc.perform(put(path(id)+"/"+itemId).with(auth("owner")).header("If-Match","1")
                .contentType(MediaType.APPLICATION_JSON).content(body("PROVINCIAL","SECOND")))
                .andExpect(status().isConflict());
        mvc.perform(put(path(id)+"/"+itemId).with(auth("owner")).header("If-Match","0")
                .contentType(MediaType.APPLICATION_JSON).content(body("PROVINCIAL","SECOND")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.calculatedScore").value(6));
        mvc.perform(delete(path(id)+"/"+itemId).with(auth("classmate")).header("If-Match","0"))
                .andExpect(status().isNotFound());
        mvc.perform(delete(path(id)+"/"+itemId).with(auth("owner")).header("If-Match","0"))
                .andExpect(status().isNoContent());
        mvc.perform(get(path(id)).with(auth("owner"))).andExpect(jsonPath("$.length()").value(0));
    }

    @Test void submitRequiresItemRecalculatesAndSnapshotsCurrentRulePermanently() throws Exception {
        long id=readyDraft();
        mvc.perform(post("/api/declarations/{id}/submit",id).with(auth("owner")).header("If-Match","1"))
                .andExpect(status().isUnprocessableEntity());
        long itemId=create(id,"NATIONAL","FIRST",0);
        mvc.perform(get(path(id)).with(auth("committee"))).andExpect(status().isNotFound());
        jdbc.sql("UPDATE score_item SET calculated_score=999 WHERE id=:id").param("id",itemId).update();
        submit(id);
        assertThat(jdbc.sql("SELECT calculated_score FROM score_item WHERE id=:id").param("id",itemId)
                .query(java.math.BigDecimal.class).single()).isEqualByComparingTo("10");
        assertThat(jdbc.sql("SELECT calculated_score FROM submission_score_item WHERE source_score_item_id=:id")
                .param("id",itemId).query(java.math.BigDecimal.class).single()).isEqualByComparingTo("10");
        assertThat(jdbc.sql("SELECT rule_set_version FROM submission_score_item WHERE source_score_item_id=:id")
                .param("id",itemId).query(String.class).single()).isEqualTo("XJTU_SCHOOL_2018_V1");
        mvc.perform(get(path(id)).with(auth("committee"))).andExpect(status().isOk())
                .andExpect(jsonPath("$[0].activityName").value("虚构学科竞赛"))
                .andExpect(jsonPath("$[0].calculatedScore").value(10));
        mvc.perform(get(path(id)).with(auth("classmate"))).andExpect(status().isNotFound());
        mvc.perform(get(path(id)).with(auth("outside"))).andExpect(status().isNotFound());
        mvc.perform(post(path(id)).with(auth("owner")).header("If-Match","1")
                .contentType(MediaType.APPLICATION_JSON).content(body("SCHOOL","FIRST")))
                .andExpect(status().isNotFound());
        mvc.perform(put(path(id)+"/"+itemId).with(auth("owner")).header("If-Match","1")
                .contentType(MediaType.APPLICATION_JSON).content(body("SCHOOL","FIRST")))
                .andExpect(status().isNotFound());
        mvc.perform(delete(path(id)+"/"+itemId).with(auth("owner")).header("If-Match","1"))
                .andExpect(status().isNotFound());
        long ruleId=jdbc.sql("SELECT rule_id FROM score_item WHERE id=:id").param("id",itemId).query(Long.class).single();
        jdbc.sql("UPDATE score_rule SET score=9 WHERE id=:id").param("id",ruleId).update();
        assertThat(jdbc.sql("SELECT calculated_score FROM submission_score_item WHERE source_score_item_id=:id")
                .param("id",itemId).query(java.math.BigDecimal.class).single()).isEqualByComparingTo("10");
        jdbc.sql("UPDATE score_rule SET score=10 WHERE id=:id").param("id",ruleId).update();
    }

    @Test void rejectedCanReviseThenEditAndResubmitWithNewSnapshot() throws Exception {
        long id=readyDraft(); long itemId=create(id,"NATIONAL","FIRST",0); submit(id);
        mvc.perform(post("/api/review/declarations/{id}/decision",id).with(auth("committee"))
                .contentType(MediaType.APPLICATION_JSON).content("{\"submissionVersion\":1,\"result\":\"REJECTED\",\"reasonCode\":\"EVIDENCE_INVALID\"}"))
                .andExpect(status().isOk());
        mvc.perform(post("/api/declarations/{id}/revise",id).with(auth("owner")).header("If-Match","1"))
                .andExpect(status().isOk());
        mvc.perform(put(path(id)+"/"+itemId).with(auth("owner")).header("If-Match","0")
                .contentType(MediaType.APPLICATION_JSON).content(body("SCHOOL","SECOND")))
                .andExpect(status().isConflict());
        mvc.perform(put(path(id)+"/"+itemId).with(auth("owner")).header("If-Match","1")
                .contentType(MediaType.APPLICATION_JSON).content(body("SCHOOL","SECOND")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.calculatedScore").value(3));
        submit(id);
        assertThat(jdbc.sql("SELECT COUNT(*) FROM submission_score_item WHERE source_score_item_id=:id")
                .param("id",itemId).query(Integer.class).single()).isEqualTo(2);
        assertThat(jdbc.sql("SELECT calculated_score FROM submission_score_item s JOIN declaration_submission d ON d.id=s.submission_id WHERE d.declaration_id=:id AND d.submission_version=1")
                .param("id",id).query(java.math.BigDecimal.class).single()).isEqualByComparingTo("10");
        assertThat(jdbc.sql("SELECT calculated_score FROM submission_score_item s JOIN declaration_submission d ON d.id=s.submission_id WHERE d.declaration_id=:id AND d.submission_version=2")
                .param("id",id).query(java.math.BigDecimal.class).single()).isEqualByComparingTo("3");
    }

    @Test void v5DatabaseConstraintsRejectBadReferencesAndNegativeScores() throws Exception {
        long id=draft(); long itemId=create(id,"NATIONAL","FIRST",0);
        assertThatThrownBy(() -> jdbc.sql("UPDATE score_item SET calculated_score=-1 WHERE id=:id")
                .param("id",itemId).update()).isInstanceOf(DataIntegrityViolationException.class);
        assertThatThrownBy(() -> jdbc.sql("UPDATE score_item SET level=NULL WHERE id=:id")
                .param("id",itemId).update()).isInstanceOf(DataIntegrityViolationException.class);
        long otherValidRuleId=jdbc.sql("SELECT id FROM score_rule WHERE rule_set_version='XJTU_SCHOOL_2018_V1' AND level='SCHOOL' AND award='FIRST'")
                .query(Long.class).single();
        assertThatThrownBy(() -> jdbc.sql("UPDATE score_item SET rule_id=:ruleId WHERE id=:id")
                .param("ruleId",otherValidRuleId).param("id",itemId).update())
                .isInstanceOf(DataIntegrityViolationException.class);
        assertThatThrownBy(() -> jdbc.sql("UPDATE score_item SET rule_id=999999 WHERE id=:id")
                .param("id",itemId).update()).isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test void submissionRejectsAnItemWhoseRuleBecameInactive() throws Exception {
        long id=readyDraft(); long itemId=create(id,"NATIONAL","FIRST",0);
        long ruleId=jdbc.sql("SELECT rule_id FROM score_item WHERE id=:id").param("id",itemId).query(Long.class).single();
        jdbc.sql("UPDATE score_rule SET active=FALSE WHERE id=:id").param("id",ruleId).update();
        try {
            mvc.perform(post("/api/declarations/{id}/submit",id).with(auth("owner")).header("If-Match","1"))
                    .andExpect(status().isUnprocessableEntity())
                    .andExpect(jsonPath("$.detail").value("当前规则版本不支持此级别和奖项组合"));
            assertThat(jdbc.sql("SELECT COUNT(*) FROM declaration_submission WHERE declaration_id=:id")
                    .param("id",id).query(Integer.class).single()).isZero();
        } finally {
            jdbc.sql("UPDATE score_rule SET active=TRUE WHERE id=:id").param("id",ruleId).update();
        }
    }
    @Test void classBoundRuleVersionControlsSubmissionRecalculation() throws Exception {
        long id=readyDraft(); long itemId=create(id,"NATIONAL","FIRST",0);
        jdbc.sql("INSERT INTO score_rule_set(version,source_document,source_locator) VALUES('TEST_ALTERNATE_V1','虚构测试办法','测试条款')").update();
        jdbc.sql("INSERT INTO score_rule(rule_set_version,category,subcategory,item_type,level,award,score,source_document,source_locator,active) VALUES('TEST_ALTERNATE_V1','ABILITY_EXPANSION','ACADEMIC_RESEARCH_INNOVATION','DISCIPLINE_COMPETITION','NATIONAL','FIRST',7,'虚构测试办法','测试条款',TRUE)").update();
        jdbc.sql("UPDATE class_group SET score_rule_set_version='TEST_ALTERNATE_V1' WHERE id=10").update();
        submit(id);
        assertThat(jdbc.sql("SELECT calculated_score FROM submission_score_item WHERE source_score_item_id=:id")
                .param("id",itemId).query(java.math.BigDecimal.class).single()).isEqualByComparingTo("7");
        assertThat(jdbc.sql("SELECT rule_set_version FROM submission_score_item WHERE source_score_item_id=:id")
                .param("id",itemId).query(String.class).single()).isEqualTo("TEST_ALTERNATE_V1");
        jdbc.sql("UPDATE class_group SET score_rule_set_version='XJTU_SCHOOL_2018_V1' WHERE id=10").update();
        assertThat(jdbc.sql("SELECT calculated_score FROM submission_score_item WHERE source_score_item_id=:id")
                .param("id",itemId).query(java.math.BigDecimal.class).single()).isEqualByComparingTo("7");
    }
    long draft() throws Exception {
        String response=mvc.perform(post("/api/declarations").with(auth("owner"))
                .contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(Map.of("classId",10,"title","虚构竞赛申报"))))
                .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        return json.readTree(response).get("id").asLong();
    }
    long readyDraft() throws Exception {
        long id=draft(); byte[] pdf="%PDF-1.4\nfictional\n%%EOF".getBytes(StandardCharsets.UTF_8);
        mvc.perform(multipart("/api/declarations/{id}/pdf",id)
                .file(new MockMultipartFile("file","fictional.pdf",MediaType.APPLICATION_PDF_VALUE,pdf))
                .with(auth("owner"))).andExpect(status().isCreated());
        for (String type:new String[]{"IDENTITY","VALIDITY"})
            mvc.perform(post("/api/declarations/{id}/evidence-regions",id).with(auth("owner"))
                    .header("If-Match","1").contentType(MediaType.APPLICATION_JSON)
                    .content("{\"type\":\""+type+"\",\"pageNumber\":1,\"x\":0.1,\"y\":0.2,\"width\":0.3,\"height\":0.4}"))
                    .andExpect(status().isCreated());
        return id;
    }
    long create(long id,String level,String award,int version) throws Exception {
        String response=mvc.perform(post(path(id)).with(auth("owner")).header("If-Match",version)
                .contentType(MediaType.APPLICATION_JSON).content(body(level,award)))
                .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        return json.readTree(response).get("id").asLong();
    }
    void submit(long id) throws Exception {
        int version=jdbc.sql("SELECT document_version FROM declaration_pdf WHERE declaration_id=:id")
                .param("id",id).query(Integer.class).single();
        mvc.perform(post("/api/declarations/{id}/submit",id).with(auth("owner")).header("If-Match",version))
                .andExpect(status().isOk());
    }
    String path(long id) {return "/api/declarations/"+id+"/score-items";}
    String body(String level,String award) {
        return "{\"activityName\":\"虚构学科竞赛\",\"category\":\"ABILITY_EXPANSION\",\"subcategory\":\"ACADEMIC_RESEARCH_INNOVATION\",\"itemType\":\"DISCIPLINE_COMPETITION\",\"level\":\""+level+"\",\"award\":\""+award+"\"}";
    }
    org.springframework.test.web.servlet.request.RequestPostProcessor auth(String user) {return httpBasic(user,"test-password");}
}

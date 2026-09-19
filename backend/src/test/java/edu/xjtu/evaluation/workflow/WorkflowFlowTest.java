package edu.xjtu.evaluation.workflow;

import static org.assertj.core.api.Assertions.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.httpBasic;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
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
import edu.xjtu.evaluation.storage.FileStorageService;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class WorkflowFlowTest {
    private static final byte[] OLD = "%PDF-1.4\nfictional old evidence\n%%EOF".getBytes(StandardCharsets.UTF_8);
    private static final byte[] NEW = "%PDF-1.4\nfictional revised evidence\n%%EOF".getBytes(StandardCharsets.UTF_8);
    @Autowired MockMvc mvc;
    @Autowired JdbcClient jdbc;
    @Autowired PasswordEncoder encoder;
    @Autowired ObjectMapper json;
    @Autowired FileStorageService storage;

    @BeforeEach void seed() {
        for (String table : new String[]{"review_record","submission_evidence_region","declaration_submission",
                "evidence_region","declaration_pdf","declaration","class_membership","class_group","app_user"})
            jdbc.sql("DELETE FROM "+table).update();
        String[] names={"owner","classmate","committee","outside-committee","committee-two"};
        for (int i=0;i<names.length;i++)
            jdbc.sql("INSERT INTO app_user(id,username,password_hash,display_name,enabled) VALUES(:id,:name,:hash,:name,TRUE)")
                    .param("id",i+1).param("name",names[i]).param("hash",encoder.encode("test-password")).update();
        jdbc.sql("INSERT INTO class_group(id,code,name) VALUES(10,'DEMO-01','虚构一班'),(20,'DEMO-02','虚构二班')").update();
        jdbc.sql("INSERT INTO class_membership(id,class_id,user_id,role) VALUES(101,10,1,'STUDENT'),(102,10,2,'STUDENT'),(103,10,3,'CLASS_COMMITTEE'),(104,20,4,'CLASS_COMMITTEE'),(105,10,5,'CLASS_COMMITTEE')").update();
    }

    @Test void submitRequiresPdfBothEvidenceTypesOwnershipAndCurrentDocument() throws Exception {
        long id=draft("owner",10,"最初标题");
        mvc.perform(post("/api/declarations/{id}/submit",id).with(auth("owner")).header("If-Match","1"))
                .andExpect(status().isUnprocessableEntity());
        upload(id,OLD,"old.pdf");
        mvc.perform(post("/api/declarations/{id}/submit",id).with(auth("owner")).header("If-Match","1"))
                .andExpect(status().isUnprocessableEntity());
        region(id,"IDENTITY",1);
        mvc.perform(post("/api/declarations/{id}/submit",id).with(auth("owner")).header("If-Match","1"))
                .andExpect(status().isUnprocessableEntity());
        region(id,"VALIDITY",1);
        mvc.perform(post("/api/declarations/{id}/submit",id).with(auth("classmate")).header("If-Match","1"))
                .andExpect(status().isNotFound());
        mvc.perform(post("/api/declarations/{id}/submit",id).with(auth("owner")).header("If-Match","2"))
                .andExpect(status().isConflict());
        submit(id,1,1);
        assertThat(count("declaration_submission")).isEqualTo(1);
        assertThat(count("submission_evidence_region")).isEqualTo(2);
    }

    @Test void pendingIsImmutableAndOnlyOwnerAndSameClassCommitteeCanRead() throws Exception {
        long id=readyDraft(); submit(id,1,1);
        mvc.perform(get("/api/review/classes/10/pending").with(auth("committee")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].id").value(id));
        mvc.perform(get("/api/review/classes/10/pending").with(auth("classmate")))
                .andExpect(status().isNotFound());
        for (String name:new String[]{"owner","committee"}) {
            mvc.perform(get("/api/declarations/{id}",id).with(auth(name))).andExpect(status().isOk());
            mvc.perform(get("/api/declarations/{id}/pdf",id).with(auth(name))).andExpect(content().bytes(OLD));
            mvc.perform(get("/api/declarations/{id}/evidence-regions",id).with(auth(name)))
                    .andExpect(jsonPath("$.length()").value(2));
        }
        for (String name:new String[]{"classmate","outside-committee"}) {
            mvc.perform(get("/api/declarations/{id}",id).with(auth(name))).andExpect(status().isNotFound());
            mvc.perform(get("/api/declarations/{id}/pdf",id).with(auth(name))).andExpect(status().isNotFound());
            mvc.perform(get("/api/declarations/{id}/evidence-regions",id).with(auth(name))).andExpect(status().isNotFound());
        }
        mvc.perform(patch("/api/declarations/{id}",id).with(auth("owner"))
                .contentType(MediaType.APPLICATION_JSON).content("{\"title\":\"不应修改\"}")).andExpect(status().isNotFound());
        mvc.perform(multipart("/api/declarations/{id}/pdf",id).file(pdf("new.pdf",NEW))
                .with(request->{request.setMethod("PUT");return request;}).with(auth("owner")))
                .andExpect(status().isNotFound());
        mvc.perform(post("/api/declarations/{id}/evidence-regions",id).with(auth("owner"))
                .header("If-Match","1").contentType(MediaType.APPLICATION_JSON)
                .content(regionBody("IDENTITY"))).andExpect(status().isNotFound());
        long regionId=jdbc.sql("SELECT id FROM evidence_region WHERE type='IDENTITY' AND document_id=(SELECT id FROM declaration_pdf WHERE declaration_id=:id)")
                .param("id",id).query(Long.class).single();
        mvc.perform(put("/api/declarations/{id}/evidence-regions/{regionId}",id,regionId)
                .with(auth("owner")).header("If-Match","1")
                .contentType(MediaType.APPLICATION_JSON).content(regionBody("IDENTITY")))
                .andExpect(status().isNotFound());
        mvc.perform(delete("/api/declarations/{id}/evidence-regions/{regionId}",id,regionId)
                .with(auth("owner")).header("If-Match","1")).andExpect(status().isNotFound());
        mvc.perform(post("/api/declarations/{id}/submit",id).with(auth("owner")).header("If-Match","1"))
                .andExpect(status().isConflict());
    }

    @Test void approvalAndRejectionValidateRolesReasonsAndSingleDecision() throws Exception {
        long id=readyDraft(); submit(id,1,1);
        mvc.perform(post(decisionPath(id)).with(auth("owner")).contentType(MediaType.APPLICATION_JSON)
                .content(decision(1,"APPROVED",null,null))).andExpect(status().isNotFound());
        mvc.perform(post(decisionPath(id)).with(auth("classmate")).contentType(MediaType.APPLICATION_JSON)
                .content(decision(1,"APPROVED",null,null))).andExpect(status().isNotFound());
        mvc.perform(post(decisionPath(id)).with(auth("outside-committee")).contentType(MediaType.APPLICATION_JSON)
                .content(decision(1,"APPROVED",null,null))).andExpect(status().isNotFound());
        mvc.perform(post(decisionPath(id)).with(auth("committee")).contentType(MediaType.APPLICATION_JSON)
                .content(decision(1,"REJECTED","OTHER",null))).andExpect(status().isBadRequest());
        mvc.perform(post(decisionPath(id)).with(auth("committee")).contentType(MediaType.APPLICATION_JSON)
                .content(decision(1,"APPROVED","OTHER","无效"))).andExpect(status().isBadRequest());
        mvc.perform(post(decisionPath(id)).with(auth("committee")).contentType(MediaType.APPLICATION_JSON)
                .content(decision(0,"APPROVED",null,null))).andExpect(status().isConflict());
        mvc.perform(post(decisionPath(id)).with(auth("committee")).contentType(MediaType.APPLICATION_JSON)
                .content(decision(1,"APPROVED",null,null)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.status").value("APPROVED"));
        mvc.perform(post(decisionPath(id)).with(auth("committee")).contentType(MediaType.APPLICATION_JSON)
                .content(decision(1,"REJECTED","OTHER","重复审核"))).andExpect(status().isConflict());
        assertThat(count("review_record")).isEqualTo(1);
        mvc.perform(get("/api/declarations/{id}/reviews",id).with(auth("owner")))
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].result").value("APPROVED"));
        mvc.perform(patch("/api/declarations/{id}",id).with(auth("owner"))
                .contentType(MediaType.APPLICATION_JSON).content("{\"title\":\"不应修改\"}")).andExpect(status().isNotFound());
    }

    @Test void twoCommitteeRequestsRaceButOnlyOneReviewIsRecorded() throws Exception {
        long id=readyDraft(); submit(id,1,1);
        var gate=new CountDownLatch(1);
        try(var pool=Executors.newFixedThreadPool(2)) {
            var first=pool.submit(()->{
                gate.await(5,TimeUnit.SECONDS);
                return mvc.perform(post(decisionPath(id)).with(auth("committee"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(decision(1,"APPROVED",null,null)))
                        .andReturn().getResponse().getStatus();
            });
            var second=pool.submit(()->{
                gate.await(5,TimeUnit.SECONDS);
                return mvc.perform(post(decisionPath(id)).with(auth("committee-two"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(decision(1,"REJECTED","EVIDENCE_INVALID",null)))
                        .andReturn().getResponse().getStatus();
            });
            gate.countDown();
            assertThat(java.util.List.of(first.get(15,TimeUnit.SECONDS),second.get(15,TimeUnit.SECONDS)))
                    .containsExactlyInAnyOrder(200,409);
        }
        assertThat(count("review_record")).isEqualTo(1);
        String status=jdbc.sql("SELECT status FROM declaration WHERE id=:id")
                .param("id",id).query(String.class).single();
        assertThat(status).isIn("APPROVED","REJECTED");
    }

    @Test void committeeCannotReviewItsOwnSubmission() throws Exception {
        long id=draft("committee",10,"班委本人申报");
        mvc.perform(multipart("/api/declarations/{id}/pdf",id).file(pdf("own.pdf",OLD)).with(auth("committee")))
                .andExpect(status().isCreated());
        regionAs("committee",id,"IDENTITY",1); regionAs("committee",id,"VALIDITY",1);
        mvc.perform(post("/api/declarations/{id}/submit",id).with(auth("committee")).header("If-Match","1"))
                .andExpect(status().isOk());
        mvc.perform(post(decisionPath(id)).with(auth("committee")).contentType(MediaType.APPLICATION_JSON)
                .content(decision(1,"APPROVED",null,null))).andExpect(status().isForbidden());
        mvc.perform(get("/api/review/classes/10/pending").with(auth("committee")))
                .andExpect(jsonPath("$.length()").value(0));
    }

    @Test void rejectionRevisionResubmissionKeepReviewAndImmutableSnapshotHistory() throws Exception {
        long id=readyDraft();
        submit(id,1,1);
        String oldSnapshot=jdbc.sql("SELECT pdf_storage_key FROM declaration_submission WHERE declaration_id=:id AND submission_version=1")
                .param("id",id).query(String.class).single();
        mvc.perform(post(decisionPath(id)).with(auth("committee")).contentType(MediaType.APPLICATION_JSON)
                .content(decision(1,"REJECTED","OTHER","请补充虚构证明"))).andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("REJECTED"));
        mvc.perform(get("/api/declarations/{id}",id).with(auth("owner")))
                .andExpect(jsonPath("$.latestReasonCode").value("OTHER"))
                .andExpect(jsonPath("$.latestCustomReason").value("请补充虚构证明"));
        mvc.perform(post("/api/declarations/{id}/revise",id).with(auth("owner")).header("If-Match","0"))
                .andExpect(status().isConflict());
        mvc.perform(post("/api/declarations/{id}/revise",id).with(auth("owner")).header("If-Match","1"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.status").value("DRAFT"))
                .andExpect(jsonPath("$.submissionVersion").value(1));
        mvc.perform(patch("/api/declarations/{id}",id).with(auth("owner"))
                .contentType(MediaType.APPLICATION_JSON).content("{\"title\":\"修订标题\"}"))
                .andExpect(status().isOk());
        mvc.perform(multipart("/api/declarations/{id}/pdf",id).file(pdf("new.pdf",NEW))
                .with(request->{request.setMethod("PUT");return request;}).with(auth("owner")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.pdf.documentVersion").value(2));
        for (String type:new String[]{"IDENTITY","VALIDITY"})
            mvc.perform(post("/api/declarations/{id}/evidence-regions",id).with(auth("owner"))
                    .header("If-Match","2").contentType(MediaType.APPLICATION_JSON)
                    .content("{\"type\":\""+type+"\",\"pageNumber\":1,\"x\":0.5,\"y\":0.2,\"width\":0.3,\"height\":0.4}"))
                    .andExpect(status().isCreated());
        submit(id,2,2);
        assertThat(count("declaration_submission")).isEqualTo(2);
        assertThat(count("review_record")).isEqualTo(1);
        assertThat(storage.open(oldSnapshot).readAllBytes()).isEqualTo(OLD);
        assertThat(jdbc.sql("SELECT title FROM declaration_submission WHERE declaration_id=:id AND submission_version=1")
                .param("id",id).query(String.class).single()).isEqualTo("最初标题");
        assertThat(jdbc.sql("SELECT title FROM declaration_submission WHERE declaration_id=:id AND submission_version=2")
                .param("id",id).query(String.class).single()).isEqualTo("修订标题");
        assertThat(jdbc.sql("SELECT COUNT(*) FROM submission_evidence_region WHERE submission_id=(SELECT id FROM declaration_submission WHERE declaration_id=:id AND submission_version=1)")
                .param("id",id).query(Integer.class).single()).isEqualTo(2);
        assertThat(jdbc.sql("SELECT DISTINCT x FROM submission_evidence_region WHERE submission_id=(SELECT id FROM declaration_submission WHERE declaration_id=:id AND submission_version=1)")
                .param("id",id).query(java.math.BigDecimal.class).single()).isEqualByComparingTo("0.1");
        assertThat(jdbc.sql("SELECT DISTINCT x FROM submission_evidence_region WHERE submission_id=(SELECT id FROM declaration_submission WHERE declaration_id=:id AND submission_version=2)")
                .param("id",id).query(java.math.BigDecimal.class).single()).isEqualByComparingTo("0.5");
        mvc.perform(post(decisionPath(id)).with(auth("committee")).contentType(MediaType.APPLICATION_JSON)
                .content(decision(1,"APPROVED",null,null))).andExpect(status().isConflict());
        mvc.perform(post(decisionPath(id)).with(auth("committee")).contentType(MediaType.APPLICATION_JSON)
                .content(decision(2,"APPROVED",null,null))).andExpect(status().isOk());
        mvc.perform(get("/api/declarations/{id}/reviews",id).with(auth("owner")))
                .andExpect(jsonPath("$.length()").value(2))
                .andExpect(jsonPath("$[0].submissionVersion").value(2))
                .andExpect(jsonPath("$[1].submissionVersion").value(1));
    }

    @Test void v4DatabaseChecksRejectInvalidStatusAndReviewReason() throws Exception {
        long id=readyDraft(); submit(id,1,1);
        assertThatThrownBy(()->jdbc.sql("UPDATE declaration SET status='INVALID' WHERE id=:id")
                .param("id",id).update()).isInstanceOf(DataIntegrityViolationException.class);
        assertThatThrownBy(()->jdbc.sql("INSERT INTO declaration_submission(declaration_id,submission_version,title,pdf_storage_key,pdf_original_filename,pdf_size_bytes,pdf_sha256,pdf_document_version) VALUES(:id,0,'bad','submissions/bad.pdf','bad.pdf',1,'fictional',1)")
                .param("id",id).update()).isInstanceOf(DataIntegrityViolationException.class);
        assertThatThrownBy(()->jdbc.sql("INSERT INTO review_record(declaration_id,reviewer_user_id,submission_version,result,reason_code,custom_reason) VALUES(:id,3,1,'APPROVED','OTHER','invalid')")
                .param("id",id).update()).isInstanceOf(DataIntegrityViolationException.class);
    }

    int count(String table) {return jdbc.sql("SELECT COUNT(*) FROM "+table).query(Integer.class).single();}
    long readyDraft() throws Exception {
        long id=draft("owner",10,"最初标题");
        upload(id,OLD,"old.pdf");
        region(id,"IDENTITY",1); region(id,"VALIDITY",1);
        return id;
    }
    long draft(String owner,long classId,String title) throws Exception {
        String response=mvc.perform(post("/api/declarations").with(auth(owner)).contentType(MediaType.APPLICATION_JSON)
                .content(json.writeValueAsString(Map.of("classId",classId,"title",title))))
                .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        return json.readTree(response).get("id").asLong();
    }
    void upload(long id,byte[] data,String name) throws Exception {
        mvc.perform(multipart("/api/declarations/{id}/pdf",id).file(pdf(name,data)).with(auth("owner")))
                .andExpect(status().isCreated());
    }
    void region(long id,String type,int version) throws Exception {regionAs("owner",id,type,version);}
    void regionAs(String user,long id,String type,int version) throws Exception {
        mvc.perform(post("/api/declarations/{id}/evidence-regions",id).with(auth(user))
                .header("If-Match",String.valueOf(version)).contentType(MediaType.APPLICATION_JSON)
                .content(regionBody(type))).andExpect(status().isCreated());
    }
    void submit(long id,int documentVersion,int submissionVersion) throws Exception {
        mvc.perform(post("/api/declarations/{id}/submit",id).with(auth("owner"))
                .header("If-Match",String.valueOf(documentVersion)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.status").value("PENDING"))
                .andExpect(jsonPath("$.submissionVersion").value(submissionVersion));
    }
    String regionBody(String type) {return "{\"type\":\""+type+"\",\"pageNumber\":1,\"x\":0.1,\"y\":0.2,\"width\":0.3,\"height\":0.4}";}
    String decisionPath(long id) {return "/api/review/declarations/"+id+"/decision";}
    String decision(int version,String result,String reason,String custom) throws Exception {
        java.util.Map<String,Object> body=new java.util.LinkedHashMap<>();
        body.put("submissionVersion",version);
        body.put("result",result);
        if (reason != null) body.put("reasonCode",reason);
        if (custom != null) body.put("customReason",custom);
        return json.writeValueAsString(body);
    }
    MockMultipartFile pdf(String name,byte[] bytes) {return new MockMultipartFile("file",name,MediaType.APPLICATION_PDF_VALUE,bytes);}
    org.springframework.test.web.servlet.request.RequestPostProcessor auth(String user) {return httpBasic(user,"test-password");}
}


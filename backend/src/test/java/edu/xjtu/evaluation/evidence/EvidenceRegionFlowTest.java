package edu.xjtu.evaluation.evidence;

import static org.assertj.core.api.Assertions.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.httpBasic;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.Principal;
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
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import com.fasterxml.jackson.databind.ObjectMapper;
import edu.xjtu.evaluation.declaration.DeclarationService;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class EvidenceRegionFlowTest {
    static final byte[] OLD="%PDF-1.4\nold fictional\n%%EOF".getBytes(StandardCharsets.UTF_8);
    static final byte[] NEW="%PDF-1.4\nnew fictional\n%%EOF".getBytes(StandardCharsets.UTF_8);
    static final String VALID="{\"type\":\"IDENTITY\",\"pageNumber\":1,\"x\":0.1,\"y\":0.2,\"width\":0.3,\"height\":0.4}";
    @Autowired MockMvc mvc;
    @Autowired JdbcClient jdbc;
    @Autowired PasswordEncoder encoder;
    @Autowired ObjectMapper json;
    @Autowired DeclarationService declarations;
    @Autowired PlatformTransactionManager transactions;
    Path storageRoot=Path.of(System.getProperty("java.io.tmpdir"),"xjtu-evaluation-test-uploads");

    @BeforeEach void seed() {
        for(String table:new String[]{"review_record","submission_evidence_region","declaration_submission","evidence_region","declaration_pdf","declaration","class_membership","class_group","app_user"})
            jdbc.sql("DELETE FROM "+table).update();
        String[] names={"owner","classmate","committee","outsider"};
        for(int i=0;i<names.length;i++)
            jdbc.sql("INSERT INTO app_user(id,username,password_hash,display_name,enabled) VALUES(:id,:n,:p,:n,TRUE)")
                    .param("id",i+1).param("n",names[i]).param("p",encoder.encode("test-password")).update();
        jdbc.sql("INSERT INTO class_group(id,code,name) VALUES(10,'DEMO-01','虚构一班'),(20,'DEMO-02','虚构二班')").update();
        jdbc.sql("INSERT INTO class_membership(id,class_id,user_id,role) VALUES(101,10,1,'STUDENT'),(102,10,2,'STUDENT'),(103,10,3,'CLASS_COMMITTEE'),(104,20,4,'STUDENT')").update();
    }

    @Test void ownerCrudAndAccessIsolation() throws Exception {
        long id=draft(), region=region(id,VALID);
        mvc.perform(get(base(id)).with(auth("owner"))).andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1)).andExpect(jsonPath("$[0].source").value("MANUAL"));
        mvc.perform(put(base(id)+"/"+region).with(auth("owner")).header("If-Match","1").contentType(MediaType.APPLICATION_JSON)
                .content("{\"type\":\"VALIDITY\",\"pageNumber\":2,\"x\":0.5,\"y\":0.5,\"width\":0.25,\"height\":0.25}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.type").value("VALIDITY"));
        for(String other:new String[]{"classmate","committee","outsider"}) {
            mvc.perform(get(base(id)).with(auth(other))).andExpect(status().isNotFound());
            mvc.perform(post(base(id)).with(auth(other)).header("If-Match","1").contentType(MediaType.APPLICATION_JSON).content(VALID)).andExpect(status().isNotFound());
            mvc.perform(put(base(id)+"/"+region).with(auth(other)).header("If-Match","1").contentType(MediaType.APPLICATION_JSON).content(VALID)).andExpect(status().isNotFound());
            mvc.perform(delete(base(id)+"/"+region).with(auth(other)).header("If-Match","1")).andExpect(status().isNotFound());
        }
        mvc.perform(delete(base(id)+"/"+region).with(auth("owner")).header("If-Match","1")).andExpect(status().isNoContent());
        mvc.perform(get(base(id)).with(auth("owner"))).andExpect(jsonPath("$.length()").value(0));
    }

    @Test void invalidCoordinatesPageAndDatabaseChecks() throws Exception {
        long id=draft();
        for(String body:new String[]{
                "{\"type\":\"IDENTITY\",\"pageNumber\":0,\"x\":0.1,\"y\":0.2,\"width\":0.3,\"height\":0.4}",
                "{\"type\":\"IDENTITY\",\"pageNumber\":1,\"x\":-0.1,\"y\":0.2,\"width\":0.3,\"height\":0.4}",
                "{\"type\":\"IDENTITY\",\"pageNumber\":1,\"x\":0.9,\"y\":0.2,\"width\":0.2,\"height\":0.4}",
                "{\"type\":\"IDENTITY\",\"pageNumber\":1,\"x\":0.1,\"y\":0.2,\"width\":0,\"height\":0.4}",
                "{\"type\":\"IDENTITY\",\"pageNumber\":1,\"x\":0.1,\"y\":0.8,\"width\":0.3,\"height\":0.3}",
                "{\"type\":\"IDENTITY\",\"pageNumber\":1,\"x\":0.9999999,\"y\":0.2,\"width\":0.0000001,\"height\":0.4}"})
            mvc.perform(post(base(id)).with(auth("owner")).header("If-Match","1").contentType(MediaType.APPLICATION_JSON).content(body)).andExpect(status().isBadRequest());
        long doc=jdbc.sql("SELECT id FROM declaration_pdf WHERE declaration_id=:id").param("id",id).query(Long.class).single();
        for(String values:new String[]{
                "'IDENTITY',0,0.1,0.2,0.3,0.4,'MANUAL'",
                "'IDENTITY',1,0.9,0.2,0.2,0.4,'MANUAL'",
                "'IDENTITY',1,0.1,0.2,0,0.4,'MANUAL'",
                "'INVALID',1,0.1,0.2,0.3,0.4,'MANUAL'"}) {
            String sql="INSERT INTO evidence_region(document_id,type,page_number,x,y,width,height,source) VALUES("+doc+","+values+")";
            assertThatThrownBy(()->jdbc.sql(sql).update()).isInstanceOf(DataIntegrityViolationException.class);
        }
    }

    @Test void replacementClearsRegionsAndServesNewPdf() throws Exception {
        long id=draft(); region(id,VALID);
        mvc.perform(get("/api/declarations/{id}",id).with(auth("owner")))
                .andExpect(jsonPath("$.pdf.documentVersion").value(1));
        mvc.perform(multipart("/api/declarations/{id}/pdf",id).file(pdf("new.pdf",NEW))
                .with(req->{req.setMethod("PUT");return req;}).with(auth("owner")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.pdf.originalFilename").value("new.pdf"))
                .andExpect(jsonPath("$.pdf.documentVersion").value(2));
        mvc.perform(get(base(id)).with(auth("owner"))).andExpect(jsonPath("$.length()").value(0));
        mvc.perform(get("/api/declarations/{id}/pdf",id).with(auth("owner"))).andExpect(content().bytes(NEW));
    }

    @Test void staleClientCannotCreateUpdateOrDeleteAfterReplacement() throws Exception {
        long id=draft();
        mvc.perform(multipart("/api/declarations/{id}/pdf",id).file(pdf("new.pdf",NEW))
                .with(req->{req.setMethod("PUT");return req;}).with(auth("owner")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.pdf.documentVersion").value(2));
        mvc.perform(post(base(id)).with(auth("owner")).header("If-Match","1")
                .contentType(MediaType.APPLICATION_JSON).content(VALID)).andExpect(status().isConflict());
        String response=mvc.perform(post(base(id)).with(auth("owner")).header("If-Match","2")
                .contentType(MediaType.APPLICATION_JSON).content(VALID))
                .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        long region=json.readTree(response).get("id").asLong();
        mvc.perform(put(base(id)+"/"+region).with(auth("owner")).header("If-Match","1")
                .contentType(MediaType.APPLICATION_JSON).content(VALID)).andExpect(status().isConflict());
        mvc.perform(delete(base(id)+"/"+region).with(auth("owner")).header("If-Match","1"))
                .andExpect(status().isConflict());
        mvc.perform(get(base(id)).with(auth("owner"))).andExpect(jsonPath("$.length()").value(1));
        mvc.perform(delete(base(id)+"/"+region).with(auth("owner")).header("If-Match","2"))
                .andExpect(status().isNoContent());
    }

    @Test void failedReplacementAndRollbackPreserveOldPdfAndRegions() throws Exception {
        long id=draft(); region(id,VALID); String oldKey=key(id);
        mvc.perform(multipart("/api/declarations/{id}/pdf",id)
                .file(pdf("bad.pdf","invalid".getBytes(StandardCharsets.UTF_8)))
                .with(req->{req.setMethod("PUT");return req;}).with(auth("owner"))).andExpect(status().isBadRequest());
        assertOld(id,oldKey);
        String newKey=new TransactionTemplate(transactions).execute(status->{
            declarations.replacePdf((Principal)()->"owner",id,pdf("new.pdf",NEW));
            String k=key(id);
            assertThat(Files.exists(storageRoot.resolve(k))).isTrue();
            status.setRollbackOnly();
            return k;
        });
        assertThat(newKey).isNotEqualTo(oldKey);
        assertThat(Files.exists(storageRoot.resolve(newKey))).isFalse();
        assertOld(id,oldKey);
    }

    void assertOld(long id,String expected) throws Exception {
        assertThat(key(id)).isEqualTo(expected);
        assertThat(Files.exists(storageRoot.resolve(expected))).isTrue();
        mvc.perform(get(base(id)).with(auth("owner"))).andExpect(jsonPath("$.length()").value(1));
        mvc.perform(get("/api/declarations/{id}/pdf",id).with(auth("owner"))).andExpect(content().bytes(OLD));
    }
    String key(long id) {return jdbc.sql("SELECT storage_key FROM declaration_pdf WHERE declaration_id=:id").param("id",id).query(String.class).single();}
    long draft() throws Exception {
        String response=mvc.perform(post("/api/declarations").with(auth("owner")).contentType(MediaType.APPLICATION_JSON)
                .content(json.writeValueAsString(Map.of("classId",10,"title","虚构证明"))))
                .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        long id=json.readTree(response).get("id").asLong();
        mvc.perform(multipart("/api/declarations/{id}/pdf",id).file(pdf("old.pdf",OLD)).with(auth("owner"))).andExpect(status().isCreated());
        return id;
    }
    long region(long id,String body) throws Exception {
        String response=mvc.perform(post(base(id)).with(auth("owner")).header("If-Match","1").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        return json.readTree(response).get("id").asLong();
    }
    String base(long id) {return "/api/declarations/"+id+"/evidence-regions";}
    MockMultipartFile pdf(String name,byte[] bytes) {return new MockMultipartFile("file",name,MediaType.APPLICATION_PDF_VALUE,bytes);}
    org.springframework.test.web.servlet.request.RequestPostProcessor auth(String name) {return httpBasic(name,"test-password");}
}

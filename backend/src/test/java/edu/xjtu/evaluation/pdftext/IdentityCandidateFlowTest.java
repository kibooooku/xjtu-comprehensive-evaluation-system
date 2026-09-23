package edu.xjtu.evaluation.pdftext;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.httpBasic;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class IdentityCandidateFlowTest {
    @Autowired MockMvc mvc;
    @Autowired JdbcClient jdbc;
    @Autowired PasswordEncoder encoder;
    @Autowired ObjectMapper json;

    @BeforeEach void seed() {
        for (String table : new String[]{"review_record","submission_score_item","submission_evidence_region",
                "declaration_submission","score_item","evidence_region","declaration_pdf","declaration",
                "class_membership","class_group","app_user"}) jdbc.sql("DELETE FROM " + table).update();
        for (int i = 1; i <= 4; i++) {
            String name = "fictional-user-" + i;
            jdbc.sql("INSERT INTO app_user(id,username,password_hash,display_name,enabled) VALUES(:id,:name,:password,:name,TRUE)")
                    .param("id",i).param("name",name).param("password",encoder.encode("test-password")).update();
        }
        jdbc.sql("INSERT INTO class_group(id,code,name) VALUES(10,'DEMO-10','Fictional One'),(20,'DEMO-20','Fictional Two')").update();
        jdbc.sql("INSERT INTO class_membership(id,class_id,user_id,role) VALUES(101,10,1,'STUDENT'),(102,10,2,'STUDENT'),(103,10,3,'CLASS_COMMITTEE'),(104,20,4,'CLASS_COMMITTEE')").update();
    }

    @Test void ownerCanInspectAndConfirmServerCandidateButOthersCannot() throws Exception {
        long id = draftWithPdf();
        String base = base(id);
        mvc.perform(get(base)).andExpect(status().isUnauthorized());
        for (int user : new int[]{2,3,4}) {
            mvc.perform(get(base).with(auth(user))).andExpect(status().isNotFound());
            mvc.perform(post(base + "/arbitrary/confirm").with(auth(user)).header("If-Match",1))
                    .andExpect(status().isNotFound());
        }
        JsonNode response = read(mvc.perform(get(base).with(auth(1))).andExpect(status().isOk())
                .andExpect(jsonPath("$.documentVersion").value(1))
                .andExpect(jsonPath("$.pageCount").value(2))
                .andExpect(jsonPath("$.analysisStatus").value("TEXT_AVAILABLE"))
                .andExpect(jsonPath("$.candidates[0].matchedBy").value("STUDENT_NUMBER"))
                .andReturn().getResponse().getContentAsString());
        String candidate = response.at("/candidates/0/id").asText();
        assertThat(candidate).startsWith("candidate-");
        mvc.perform(post(base + "/missing/confirm").with(auth(1)).header("If-Match",1))
                .andExpect(status().isConflict());
        mvc.perform(post(base + "/" + candidate + "/confirm").with(auth(1)).header("If-Match",1))
                .andExpect(status().isOk()).andExpect(jsonPath("$.type").value("IDENTITY"))
                .andExpect(jsonPath("$.source").value("PDF_TEXT_AUTO"))
                .andExpect(jsonPath("$.pageNumber").value(1));
        assertThat(jdbc.sql("SELECT COUNT(*) FROM evidence_region WHERE source='PDF_TEXT_AUTO'")
                .query(Long.class).single()).isEqualTo(1);
        mvc.perform(put("/api/me/identity").with(auth(1)).contentType(MediaType.APPLICATION_JSON)
                .content("{\"studentNumber\":\"9999999999\",\"studentName\":\"Alex Example\"}"))
                .andExpect(status().isOk());
        mvc.perform(get(base).with(auth(1))).andExpect(status().isOk())
                .andExpect(jsonPath("$.candidates[0].matchedBy").value("NAME"));
        mvc.perform(post(base + "/" + candidate + "/confirm").with(auth(1)).header("If-Match",1))
                .andExpect(status().isConflict());
    }

    @Test void replacementInvalidatesOldVersionAndPendingCannotUseCandidates() throws Exception {
        long id = draftWithPdf();
        JsonNode before = read(mvc.perform(get(base(id)).with(auth(1))).andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString());
        String oldCandidate = before.at("/candidates/0/id").asText();
        mvc.perform(multipart("/api/declarations/{id}/pdf",id)
                .file(new MockMultipartFile("file","replacement.pdf","application/pdf",PdfTextAnalyzerTest.pdf(true,false)))
                .with(request -> {request.setMethod("PUT"); return request;}).with(auth(1)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.pdf.documentVersion").value(2));
        mvc.perform(post(base(id) + "/" + oldCandidate + "/confirm").with(auth(1)).header("If-Match",1))
                .andExpect(status().isConflict());
        mvc.perform(get(base(id)).with(auth(1))).andExpect(status().isOk())
                .andExpect(jsonPath("$.documentVersion").value(2))
                .andExpect(jsonPath("$.analysisStatus").value("NO_TEXT"))
                .andExpect(jsonPath("$.candidates.length()").value(0));
        jdbc.sql("UPDATE declaration SET status='PENDING' WHERE id=:id").param("id",id).update();
        mvc.perform(get(base(id)).with(auth(1))).andExpect(status().isNotFound());
        mvc.perform(post(base(id) + "/" + oldCandidate + "/confirm").with(auth(1)).header("If-Match",2))
                .andExpect(status().isNotFound());
    }

    private long draftWithPdf() throws Exception {
        mvc.perform(put("/api/me/identity").with(auth(1)).contentType(MediaType.APPLICATION_JSON)
                .content("{\"studentNumber\":\"2400000001\",\"studentName\":\"Alex Example\"}"))
                .andExpect(status().isOk());
        String body = mvc.perform(post("/api/declarations").with(auth(1)).contentType(MediaType.APPLICATION_JSON)
                .content(json.writeValueAsString(Map.of("classId",10,"title","Fictional evidence"))))
                .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        long id = read(body).get("id").asLong();
        mvc.perform(multipart("/api/declarations/{id}/pdf",id)
                .file(new MockMultipartFile("file","fictional.pdf","application/pdf",PdfTextAnalyzerTest.pdf(false,false)))
                .with(auth(1))).andExpect(status().isCreated());
        return id;
    }
    private JsonNode read(String body) throws Exception {return json.readTree(body);}
    private String base(long id) {return "/api/declarations/" + id + "/identity-candidates";}
    private org.springframework.test.web.servlet.request.RequestPostProcessor auth(int user) {
        return httpBasic("fictional-user-" + user,"test-password");
    }
}

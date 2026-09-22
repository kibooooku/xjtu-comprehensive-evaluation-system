package edu.xjtu.evaluation.declaration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.httpBasic;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.Principal;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class DeclarationFlowTest {
    private static final byte[] PDF = "%PDF-1.4\nfictional evidence\n%%EOF\n".getBytes(StandardCharsets.UTF_8);

    @Autowired MockMvc mvc;
    @Autowired JdbcClient jdbc;
    @Autowired PasswordEncoder passwordEncoder;
    @Autowired ObjectMapper objectMapper;
    @Autowired DeclarationService declarationService;
    @Autowired PlatformTransactionManager transactionManager;
    @Value("${app.storage.root}") Path storageRoot;

    @BeforeEach
    void seedFictionalUsers() {
        jdbc.sql("DELETE FROM review_record").update();
        jdbc.sql("DELETE FROM submission_score_item").update();
        jdbc.sql("DELETE FROM submission_evidence_region").update();
        jdbc.sql("DELETE FROM declaration_submission").update();
        jdbc.sql("DELETE FROM score_item").update();
        jdbc.sql("DELETE FROM evidence_region").update();
        jdbc.sql("DELETE FROM declaration_pdf").update();
        jdbc.sql("DELETE FROM declaration").update();
        jdbc.sql("DELETE FROM class_membership").update();
        jdbc.sql("DELETE FROM class_group").update();
        jdbc.sql("DELETE FROM app_user").update();

        insertUser(1, "student-a", "示例学生甲");
        insertUser(2, "committee-a", "示例班委甲");
        insertUser(3, "student-b", "示例学生乙");
        jdbc.sql("INSERT INTO class_group(id,code,name) VALUES(10,'DEMO-01','示例一班')").update();
        jdbc.sql("INSERT INTO class_group(id,code,name) VALUES(20,'DEMO-02','示例二班')").update();
        jdbc.sql("INSERT INTO class_membership(id,class_id,user_id,role) VALUES(101,10,1,'STUDENT')").update();
        jdbc.sql("INSERT INTO class_membership(id,class_id,user_id,role) VALUES(102,10,2,'CLASS_COMMITTEE')").update();
        jdbc.sql("INSERT INTO class_membership(id,class_id,user_id,role) VALUES(103,20,3,'STUDENT')").update();
    }

    @Test
    void anonymousUsersCannotAccessPrivateEndpoints() throws Exception {
        mvc.perform(get("/api/me")).andExpect(status().isUnauthorized());
        mvc.perform(post("/api/declarations").contentType(MediaType.APPLICATION_JSON)
                .content("{\"classId\":10,\"title\":\"示例申报\"}"))
                .andExpect(status().isUnauthorized());
        mvc.perform(get("/api/declarations/1/pdf")).andExpect(status().isUnauthorized());
    }

    @Test
    void profileExposesStudentAndCommitteeMembershipRoles() throws Exception {
        mvc.perform(get("/api/me").with(httpBasic("student-a", "test-password")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.memberships[0].role").value("STUDENT"));
        mvc.perform(get("/api/me").with(httpBasic("committee-a", "test-password")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.memberships[0].role").value("CLASS_COMMITTEE"));
    }

    @Test
    void ownerCanCreateUploadAndDownloadPrivatePdf() throws Exception {
        long id = createDraft("student-a", 10, "虚构竞赛证明");

        mvc.perform(multipart("/api/declarations/{id}/pdf", id)
                        .file(pdf("evidence.pdf", MediaType.APPLICATION_PDF_VALUE, PDF))
                        .with(httpBasic("student-a", "test-password")))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.status").value("DRAFT"))
                .andExpect(jsonPath("$.hasPdf").value(true))
                .andExpect(jsonPath("$.pdf.originalFilename").value("evidence.pdf"))
                .andExpect(jsonPath("$.pdf.sizeBytes").value(PDF.length));

        mvc.perform(get("/api/declarations/{id}/pdf", id)
                        .with(httpBasic("student-a", "test-password")))
                .andExpect(status().isOk())
                .andExpect(content().contentType(MediaType.APPLICATION_PDF))
                .andExpect(content().bytes(PDF))
                .andExpect(header().string(HttpHeaders.CACHE_CONTROL, "no-store, private"))
                .andExpect(header().string(HttpHeaders.CONTENT_DISPOSITION,
                        org.hamcrest.Matchers.containsString("inline")));
    }

    @Test
    void nonMemberCannotCreateDeclarationForClass() throws Exception {
        mvc.perform(post("/api/declarations").with(httpBasic("student-b", "test-password"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"classId\":10,\"title\":\"越权申报\"}"))
                .andExpect(status().isForbidden());
    }

    @Test
    void anotherUserIncludingClassCommitteeCannotReadOrUploadOwnersDeclaration() throws Exception {
        long id = createDraft("student-a", 10, "仅本人可见");
        mvc.perform(multipart("/api/declarations/{id}/pdf", id)
                        .file(pdf("private.pdf", MediaType.APPLICATION_PDF_VALUE, PDF))
                        .with(httpBasic("student-a", "test-password")))
                .andExpect(status().isCreated());

        for (String username : new String[] {"committee-a", "student-b"}) {
            mvc.perform(get("/api/declarations/{id}", id)
                            .with(httpBasic(username, "test-password")))
                    .andExpect(status().isNotFound());
            mvc.perform(get("/api/declarations/{id}/pdf", id)
                            .with(httpBasic(username, "test-password")))
                    .andExpect(status().isNotFound());
            mvc.perform(multipart("/api/declarations/{id}/pdf", id)
                            .file(pdf("replacement.pdf", MediaType.APPLICATION_PDF_VALUE, PDF))
                            .with(httpBasic(username, "test-password")))
                    .andExpect(status().isNotFound());
        }
    }


    @Test
    void mineReturnsOnlyDeclarationsOwnedByAuthenticatedUser() throws Exception {
        long ownedId = createDraft("student-a", 10, "甲的申报");
        createDraft("committee-a", 10, "班委自己的申报");
        createDraft("student-b", 20, "乙的申报");

        mvc.perform(get("/api/declarations/mine")
                        .with(httpBasic("student-a", "test-password")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].id").value(ownedId))
                .andExpect(jsonPath("$[0].title").value("甲的申报"));
    }

    @Test
    void transactionRollbackRemovesStoredPdfAndDatabaseMetadata() throws Exception {
        long id = createDraft("student-a", 10, "回滚清理测试");
        var transaction = new TransactionTemplate(transactionManager);
        String storageKey = transaction.execute(status -> {
            declarationService.upload((Principal) () -> "student-a", id,
                    pdf("rollback.pdf", MediaType.APPLICATION_PDF_VALUE, PDF));
            String key = jdbc.sql("SELECT storage_key FROM declaration_pdf WHERE declaration_id=:id")
                    .param("id", id).query(String.class).single();
            assertThat(storageRoot.resolve(key)).exists();
            status.setRollbackOnly();
            return key;
        });

        assertThat(storageKey).isNotNull();
        assertThat(Files.exists(storageRoot.resolve(storageKey))).isFalse();
        Integer count = jdbc.sql("SELECT COUNT(*) FROM declaration_pdf WHERE declaration_id=:id")
                .param("id", id).query(Integer.class).single();
        assertThat(count).isZero();
    }

    @Test
    void uploadRejectsWrongContentTypeInvalidMagicAndDuplicate() throws Exception {
        long contentTypeId = createDraft("student-a", 10, "错误类型");
        mvc.perform(multipart("/api/declarations/{id}/pdf", contentTypeId)
                        .file(pdf("fake.pdf", MediaType.TEXT_PLAIN_VALUE, PDF))
                        .with(httpBasic("student-a", "test-password")))
                .andExpect(status().isBadRequest());

        long magicId = createDraft("student-a", 10, "错误签名");
        mvc.perform(multipart("/api/declarations/{id}/pdf", magicId)
                        .file(pdf("fake.pdf", MediaType.APPLICATION_PDF_VALUE,
                                "not a pdf".getBytes(StandardCharsets.UTF_8)))
                        .with(httpBasic("student-a", "test-password")))
                .andExpect(status().isBadRequest());

        long duplicateId = createDraft("student-a", 10, "重复上传");
        var first = multipart("/api/declarations/{id}/pdf", duplicateId)
                .file(pdf("first.pdf", MediaType.APPLICATION_PDF_VALUE, PDF))
                .with(httpBasic("student-a", "test-password"));
        mvc.perform(first).andExpect(status().isCreated());
        mvc.perform(multipart("/api/declarations/{id}/pdf", duplicateId)
                        .file(pdf("second.pdf", MediaType.APPLICATION_PDF_VALUE, PDF))
                        .with(httpBasic("student-a", "test-password")))
                .andExpect(status().isConflict());

        Integer count = jdbc.sql("SELECT COUNT(*) FROM declaration_pdf WHERE declaration_id=:id")
                .param("id", duplicateId).query(Integer.class).single();
        assertThat(count).isEqualTo(1);
    }

    private void insertUser(long id, String username, String displayName) {
        jdbc.sql("INSERT INTO app_user(id,username,password_hash,display_name,enabled) VALUES(:id,:name,:hash,:display,TRUE)")
                .param("id", id).param("name", username)
                .param("hash", passwordEncoder.encode("test-password"))
                .param("display", displayName).update();
    }

    private long createDraft(String username, long classId, String title) throws Exception {
        String body = objectMapper.writeValueAsString(java.util.Map.of("classId", classId, "title", title));
        String response = mvc.perform(post("/api/declarations").with(httpBasic(username, "test-password"))
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.status").value("DRAFT"))
                .andReturn().getResponse().getContentAsString();
        JsonNode json = objectMapper.readTree(response);
        return json.get("id").asLong();
    }

    private MockMultipartFile pdf(String filename, String contentType, byte[] bytes) {
        return new MockMultipartFile("file", filename, contentType, bytes);
    }
}

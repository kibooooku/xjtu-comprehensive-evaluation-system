package edu.xjtu.evaluation.user;

import java.security.Principal;
import java.util.List;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.server.ResponseStatusException;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/me")
public class MeController {
    private final JdbcClient jdbc;

    public MeController(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    @GetMapping
    public MeResponse me(Principal principal) {
        UserRow user = jdbc.sql("SELECT id,username,display_name,student_number,student_name FROM app_user WHERE username=:username AND enabled=TRUE")
                .param("username", principal.getName())
                .query((rs, n) -> new UserRow(rs.getLong("id"), rs.getString("username"), rs.getString("display_name"),
                        rs.getString("student_number"),rs.getString("student_name")))
                .single();
        List<Membership> memberships = jdbc.sql("""
                SELECT cg.id,cg.code,cg.name,cm.role FROM class_membership cm
                JOIN class_group cg ON cg.id=cm.class_id WHERE cm.user_id=:userId ORDER BY cg.code
                """).param("userId", user.id())
                .query((rs, n) -> new Membership(rs.getLong("id"), rs.getString("code"),
                        rs.getString("name"), Role.valueOf(rs.getString("role")))).list();
        return new MeResponse(user.id(), user.username(), user.displayName(),
                user.studentNumber(),user.studentName(),memberships);
    }

    @PutMapping("/identity")
    public MeResponse identity(Principal principal,@Valid @RequestBody IdentityInput input) {
        String number=input.studentNumber().trim();
        String name=input.studentName().trim();
        if(number.isBlank() || name.isBlank())
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"姓名和学号不能为空");
        try {
            jdbc.sql("UPDATE app_user SET student_number=:number,student_name=:name WHERE username=:username AND enabled=TRUE")
                    .param("number",number).param("name",name).param("username",principal.getName()).update();
        } catch (DataIntegrityViolationException conflict) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,"学号已被其他账户使用",conflict);
        }
        return me(principal);
    }

    public record IdentityInput(@NotBlank @Size(max=32) String studentNumber,
            @NotBlank @Size(max=100) String studentName) {}
    public enum Role { STUDENT, CLASS_COMMITTEE }
    public record Membership(long classId, String classCode, String className, Role role) {}
    public record MeResponse(long id, String username, String displayName,
            String studentNumber,String studentName,List<Membership> memberships) {}
    private record UserRow(long id, String username, String displayName,
            String studentNumber,String studentName) {}
}

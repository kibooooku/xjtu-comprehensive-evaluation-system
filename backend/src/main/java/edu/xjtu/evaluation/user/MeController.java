package edu.xjtu.evaluation.user;

import java.security.Principal;
import java.util.List;

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
        UserRow user = jdbc.sql("SELECT id,username,display_name FROM app_user WHERE username=:username AND enabled=TRUE")
                .param("username", principal.getName())
                .query((rs, n) -> new UserRow(rs.getLong("id"), rs.getString("username"), rs.getString("display_name")))
                .single();
        List<Membership> memberships = jdbc.sql("""
                SELECT cg.id,cg.code,cg.name,cm.role FROM class_membership cm
                JOIN class_group cg ON cg.id=cm.class_id WHERE cm.user_id=:userId ORDER BY cg.code
                """).param("userId", user.id())
                .query((rs, n) -> new Membership(rs.getLong("id"), rs.getString("code"),
                        rs.getString("name"), Role.valueOf(rs.getString("role")))).list();
        return new MeResponse(user.id(), user.username(), user.displayName(), memberships);
    }

    public enum Role { STUDENT, CLASS_COMMITTEE }
    public record Membership(long classId, String classCode, String className, Role role) {}
    public record MeResponse(long id, String username, String displayName, List<Membership> memberships) {}
    private record UserRow(long id, String username, String displayName) {}
}

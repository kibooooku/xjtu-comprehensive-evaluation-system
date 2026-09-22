package edu.xjtu.evaluation.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.CommandLineRunner;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

@Component
@Profile("dev")
public class DevDataInitializer implements CommandLineRunner {
    private final JdbcClient jdbc;
    private final PasswordEncoder passwordEncoder;
    private final String demoPassword;

    public DevDataInitializer(JdbcClient jdbc, PasswordEncoder passwordEncoder,
            @Value("${APP_DEMO_PASSWORD:}") String demoPassword) {
        this.jdbc = jdbc;
        this.passwordEncoder = passwordEncoder;
        this.demoPassword = demoPassword;
    }

    @Override
    public void run(String... args) {
        if (demoPassword.isBlank()) {
            throw new IllegalStateException("APP_DEMO_PASSWORD is required when the dev profile is active");
        }
        if (count("SELECT COUNT(*) FROM class_group WHERE code='DEMO-2401'") == 0) {
            jdbc.sql("INSERT INTO class_group(code,name) VALUES('DEMO-2401','虚构软件2401班')").update();
        }
        createUser("student-demo", "演示学生");
        createUser("committee-demo", "演示班委");
        jdbc.sql("UPDATE app_user SET student_number=COALESCE(student_number,'000000000001'),"
                + "student_name=COALESCE(student_name,'虚构学生') WHERE username='student-demo'").update();
        createMembership("student-demo", "STUDENT");
        createMembership("committee-demo", "CLASS_COMMITTEE");
    }

    private void createUser(String username, String displayName) {
        if (count("SELECT COUNT(*) FROM app_user WHERE username=:username", username) == 0) {
            jdbc.sql("INSERT INTO app_user(username,password_hash,display_name) VALUES(:username,:password,:name)")
                    .param("username", username)
                    .param("password", passwordEncoder.encode(demoPassword))
                    .param("name", displayName).update();
        }
    }

    private void createMembership(String username, String role) {
        jdbc.sql("""
                INSERT INTO class_membership(class_id,user_id,role)
                SELECT cg.id,u.id,:role FROM class_group cg CROSS JOIN app_user u
                WHERE cg.code='DEMO-2401' AND u.username=:username
                  AND NOT EXISTS (
                    SELECT 1 FROM class_membership cm WHERE cm.class_id=cg.id AND cm.user_id=u.id
                  )
                """).param("role", role).param("username", username).update();
    }

    private int count(String sql) {
        return jdbc.sql(sql).query(Integer.class).single();
    }

    private int count(String sql, String username) {
        return jdbc.sql(sql).param("username", username).query(Integer.class).single();
    }
}

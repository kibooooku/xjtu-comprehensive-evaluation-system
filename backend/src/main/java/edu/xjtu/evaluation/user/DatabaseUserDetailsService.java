package edu.xjtu.evaluation.user;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.core.userdetails.User;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.stereotype.Service;

@Service
public class DatabaseUserDetailsService implements UserDetailsService {
    private final JdbcClient jdbc;

    public DatabaseUserDetailsService(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public UserDetails loadUserByUsername(String username) {
        return jdbc.sql("SELECT username,password_hash,enabled FROM app_user WHERE username=:username")
                .param("username", username)
                .query((rs, n) -> User.withUsername(rs.getString("username"))
                        .password(rs.getString("password_hash"))
                        .disabled(!rs.getBoolean("enabled"))
                        .authorities("ROLE_USER").build())
                .optional().orElseThrow(() -> new UsernameNotFoundException(username));
    }
}

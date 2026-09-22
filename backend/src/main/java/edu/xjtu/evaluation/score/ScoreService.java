package edu.xjtu.evaluation.score;

import java.math.BigDecimal;
import java.security.Principal;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@Service
public class ScoreService {
    private final JdbcClient jdbc;
    public ScoreService(JdbcClient jdbc) { this.jdbc = jdbc; }

    @Transactional(readOnly = true)
    public List<Rule> rules(Principal principal,long classId) {
        long userId=userId(principal);
        boolean member=jdbc.sql("SELECT id FROM class_membership WHERE class_id=:classId AND user_id=:userId")
                .param("classId",classId).param("userId",userId).query(Long.class).optional().isPresent();
        if(!member) throw new ResponseStatusException(HttpStatus.NOT_FOUND,"Class not found");
        String version=jdbc.sql("SELECT score_rule_set_version FROM class_group WHERE id=:classId")
                .param("classId",classId).query(String.class).single();
        return jdbc.sql("SELECT id,rule_set_version,category,subcategory,item_type,level,award,score,source_document,source_locator FROM score_rule WHERE active=TRUE AND rule_set_version=:version ORDER BY category,subcategory,item_type,level,award")
                .param("version",version).query((rs,n) -> new Rule(rs.getLong("id"),rs.getString("rule_set_version"),
                        Category.valueOf(rs.getString("category")), Subcategory.valueOf(rs.getString("subcategory")),
                        ItemType.valueOf(rs.getString("item_type")), Level.valueOf(rs.getString("level")),
                        Award.valueOf(rs.getString("award")),rs.getBigDecimal("score"),
                        rs.getString("source_document"),rs.getString("source_locator"))).list();
    }

    @Transactional(readOnly = true)
    public List<Item> list(Principal principal, long declarationId) {
        access(principal,declarationId,false,null);
        return jdbc.sql("SELECT * FROM score_item WHERE declaration_id=:id ORDER BY id")
                .param("id",declarationId).query(this::item).list();
    }

    @Transactional
    public Item create(Principal principal,long declarationId,long expectedVersion,Input input) {
        access(principal,declarationId,true,expectedVersion);
        String name = name(input);
        Rule rule = match(ruleSetVersion(declarationId),input.category(),input.subcategory(),input.itemType(),input.level(),input.award());
        var key = new GeneratedKeyHolder();
        jdbc.sql("""
                INSERT INTO score_item(declaration_id,activity_name,category,subcategory,item_type,level,award,rule_id,calculated_score)
                VALUES(:declarationId,:name,:category,:subcategory,:itemType,:level,:award,:ruleId,:score)
                """).param("declarationId",declarationId).param("name",name)
                .param("category",input.category().name()).param("subcategory",input.subcategory().name())
                .param("itemType",input.itemType().name()).param("level",input.level().name())
                .param("award",input.award().name()).param("ruleId",rule.id()).param("score",rule.score())
                .update(key,"id");
        return getItem(key.getKey().longValue(),declarationId);
    }

    @Transactional
    public Item update(Principal principal,long declarationId,long itemId,long expectedVersion,Input input) {
        access(principal,declarationId,true,expectedVersion);
        String name = name(input);
        Rule rule = match(ruleSetVersion(declarationId),input.category(),input.subcategory(),input.itemType(),input.level(),input.award());
        int updated = jdbc.sql("""
                UPDATE score_item SET activity_name=:name,category=:category,subcategory=:subcategory,
                    item_type=:itemType,level=:level,award=:award,rule_id=:ruleId,
                    calculated_score=:score,updated_at=CURRENT_TIMESTAMP
                WHERE id=:itemId AND declaration_id=:declarationId
                """).param("name",name).param("category",input.category().name())
                .param("subcategory",input.subcategory().name()).param("itemType",input.itemType().name())
                .param("level",input.level().name()).param("award",input.award().name())
                .param("ruleId",rule.id()).param("score",rule.score())
                .param("itemId",itemId).param("declarationId",declarationId).update();
        if(updated==0) throw new ResponseStatusException(HttpStatus.NOT_FOUND,"Score item not found");
        return getItem(itemId,declarationId);
    }

    @Transactional
    public void delete(Principal principal,long declarationId,long itemId,long expectedVersion) {
        access(principal,declarationId,true,expectedVersion);
        int deleted = jdbc.sql("DELETE FROM score_item WHERE id=:itemId AND declaration_id=:declarationId")
                .param("itemId",itemId).param("declarationId",declarationId).update();
        if(deleted==0) throw new ResponseStatusException(HttpStatus.NOT_FOUND,"Score item not found");
    }

    // Caller holds the declaration row lock inside the submission transaction.
    public void validateAndRecalculateForSubmit(long declarationId) {
        List<Item> items = jdbc.sql("SELECT * FROM score_item WHERE declaration_id=:id ORDER BY id")
                .param("id",declarationId).query(this::item).list();
        if(items.isEmpty()) throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,
                "提交前必须填写至少一个加分条目");
        String version = ruleSetVersion(declarationId);
        for(Item item:items) {
            Rule rule = match(version,item.category(),item.subcategory(),item.itemType(),item.level(),item.award());
            jdbc.sql("UPDATE score_item SET rule_id=:ruleId,calculated_score=:score,updated_at=CURRENT_TIMESTAMP WHERE id=:id")
                    .param("ruleId",rule.id()).param("score",rule.score()).param("id",item.id()).update();
        }
    }

    public void snapshot(long declarationId,long submissionId) {
        jdbc.sql("""
                INSERT INTO submission_score_item
                  (submission_id,source_score_item_id,activity_name,category,subcategory,item_type,
                   level,award,project_key,rule_id,rule_set_version,rule_source_document,
                   rule_source_locator,calculated_score)
                SELECT :submissionId,s.id,s.activity_name,s.category,s.subcategory,s.item_type,
                       s.level,s.award,s.project_key,r.id,r.rule_set_version,r.source_document,
                       r.source_locator,s.calculated_score
                FROM score_item s JOIN score_rule r ON r.id=s.rule_id
                WHERE s.declaration_id=:declarationId
                """).param("submissionId",submissionId).param("declarationId",declarationId).update();
    }

    private String ruleSetVersion(long declarationId) {
        return jdbc.sql("""
                SELECT cg.score_rule_set_version FROM declaration d
                JOIN class_membership cm ON cm.id=d.class_membership_id
                JOIN class_group cg ON cg.id=cm.class_id WHERE d.id=:id
                """).param("id",declarationId).query(String.class).single();
    }

    private Rule match(String version,Category category,Subcategory subcategory,ItemType itemType,Level level,Award award) {
        if(category==null || subcategory==null || itemType==null || level==null || award==null)
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"加分条目分类、级别和奖项均必填");
        List<Rule> matches=jdbc.sql("""
                SELECT id,rule_set_version,category,subcategory,item_type,level,award,score,source_document,source_locator
                FROM score_rule WHERE active=TRUE AND rule_set_version=:version
                    AND category=:category AND subcategory=:subcategory
                    AND item_type=:itemType AND level=:level AND award=:award
                """).param("version",version).param("category",category.name()).param("subcategory",subcategory.name())
                .param("itemType",itemType.name()).param("level",level.name()).param("award",award.name())
                .query((rs,n)->new Rule(rs.getLong("id"),rs.getString("rule_set_version"),category,subcategory,
                        itemType,level,award,rs.getBigDecimal("score"),rs.getString("source_document"),
                        rs.getString("source_locator"))).list();
        if(matches.isEmpty()) throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,
                "当前规则版本不支持此级别和奖项组合");
        if(matches.size()!=1) throw new ResponseStatusException(HttpStatus.CONFLICT,"存在多个启用的匹配评分规则");
        return matches.get(0);
    }

    private String name(Input input) {
        if(input!=null && input.calculatedScore()!=null)
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"分数由后端规则计算，不能由客户端指定");
        if(input==null || input.activityName()==null || input.activityName().isBlank()
                || input.activityName().trim().length()>200)
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"活动名称必须为1至200个字符");
        return input.activityName().trim();
    }

    private Item getItem(long itemId,long declarationId) {
        return jdbc.sql("SELECT * FROM score_item WHERE id=:itemId AND declaration_id=:declarationId")
                .param("itemId",itemId).param("declarationId",declarationId)
                .query(this::item).optional().orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND,"Score item not found"));
    }
    private Item item(java.sql.ResultSet rs,int n) throws java.sql.SQLException {
        return new Item(rs.getLong("id"),rs.getLong("declaration_id"),rs.getString("activity_name"),
                Category.valueOf(rs.getString("category")),Subcategory.valueOf(rs.getString("subcategory")),
                ItemType.valueOf(rs.getString("item_type")),Level.valueOf(rs.getString("level")),
                Award.valueOf(rs.getString("award")),rs.getBigDecimal("calculated_score"),
                rs.getLong("rule_id"),rs.getString("project_key"),instant(rs.getTimestamp("created_at")),
                instant(rs.getTimestamp("updated_at")));
    }
    private Instant instant(Timestamp timestamp) {return timestamp.toInstant();}

    private long userId(Principal principal) {
        return jdbc.sql("SELECT id FROM app_user WHERE username=:name AND enabled=TRUE")
                .param("name",principal.getName()).query(Long.class).single();
    }
    private void access(Principal principal,long declarationId,boolean write,Long expectedVersion) {
        long userId=userId(principal);
        String sql="""
                SELECT d.status,d.submission_version,cm.user_id,cm.class_id FROM declaration d
                JOIN class_membership cm ON cm.id=d.class_membership_id WHERE d.id=:id
                """+(write ? " FOR UPDATE" : "");
        Access target=jdbc.sql(sql).param("id",declarationId)
                .query((rs,n)->new Access(rs.getString("status"),rs.getLong("submission_version"),
                        rs.getLong("user_id"),rs.getLong("class_id"))).optional()
                .orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND,"Declaration not found"));
        if(write) {
            if(target.ownerId()!=userId || !"DRAFT".equals(target.status()))
                throw new ResponseStatusException(HttpStatus.NOT_FOUND,"Declaration not found");
            if(expectedVersion==null || target.submissionVersion()!=expectedVersion)
                throw new ResponseStatusException(HttpStatus.CONFLICT,"申报版本已变化，请刷新后重试");
        } else if(target.ownerId()!=userId) {
            boolean committee=!"DRAFT".equals(target.status()) && jdbc.sql("""
                    SELECT id FROM class_membership WHERE class_id=:classId AND user_id=:userId
                    AND role='CLASS_COMMITTEE'
                    """).param("classId",target.classId()).param("userId",userId)
                    .query(Long.class).optional().isPresent();
            if(!committee) throw new ResponseStatusException(HttpStatus.NOT_FOUND,"Declaration not found");
        }
    }

    public enum Category { ABILITY_EXPANSION }
    public enum Subcategory { ACADEMIC_RESEARCH_INNOVATION }
    public enum ItemType { DISCIPLINE_COMPETITION }
    public enum Level { HIGH_LEVEL_INTERNATIONAL,NATIONAL,PROVINCIAL,SCHOOL,LOCAL_AUTHORITY,INDUSTRY_ENTERPRISE,SOCIETY_ASSOCIATION }
    public enum Award { SPECIAL,FIRST,SECOND,THIRD,EXCELLENCE }
    public record Input(@NotBlank @Size(max=200) String activityName,@NotNull Category category,
            @NotNull Subcategory subcategory,@NotNull ItemType itemType,@NotNull Level level,@NotNull Award award,BigDecimal calculatedScore) {}
    public record Rule(long id,String ruleSetVersion,Category category,Subcategory subcategory,
            ItemType itemType,Level level,Award award,BigDecimal score,String sourceDocument,String sourceLocator) {}
    public record Item(long id,long declarationId,String activityName,Category category,Subcategory subcategory,
            ItemType itemType,Level level,Award award,BigDecimal calculatedScore,long ruleId,String projectKey,
            Instant createdAt,Instant updatedAt) {}
    private record Access(String status,long submissionVersion,long ownerId,long classId) {}
}

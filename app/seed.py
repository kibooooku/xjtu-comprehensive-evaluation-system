"""Deterministic, non-personal local demo organization; no real student records."""
from . import db
from .security import password_hash

DEMO_PASSWORD = 'Demo@2026'
DEMO_ACCOUNTS = [
    {'username': 'student', 'name': '王欣然', 'role': '学生'},
    {'username': 'monitor', 'name': '张知远', 'role': '班长'},
    {'username': 'reviewer1', 'name': '陈思齐', 'role': '学生审查员'},
    {'username': 'reviewer2', 'name': '李沐阳', 'role': '学生审查员'},
    {'username': 'counselor', 'name': '周老师', 'role': '辅导员'},
    {'username': 'admin', 'name': '系统管理员', 'role': '系统管理员'},
]

CATEGORIES = [
    ('COLLECTIVE', '集体活动', 3, 'SUM', False),
    ('IDEOLOGY', '思想政治理论学习', 3, 'SUM', False),
    ('SERVICE', '社会服务', 4, 'VOLUNTEER_CAPPED_SUM', False),
    ('RESEARCH', '学术科研及创新创业', 10, 'PROJECT_MAX', True),
    ('ARTS', '文体竞赛', 6, 'PROJECT_MAX', True),
    ('POST', '学生组织社团任职', 4, 'MAX', False),
    ('REWARD', '奖励分', 5, 'SUM', True),
]

PERMISSIONS = {
    'application.read': '查看申请', 'application.write': '填写本人申请',
    'application.submit': '正式提交本人申请', 'application.withdraw': '撤回本人待审申请',
    'materials.read': '查看原始证明与完整证据', 'review.peer': '学生初审',
    'review.counselor': '辅导员资格审核', 'review.revoke': '驳回已通过申请',
    'activity.read': '查看活动资格', 'activity.manage': '维护活动资格',
    'scores.read': '核查成绩', 'publicity.read': '查看本班公示',
    'publicity.manage': '开启关闭公示', 'export.read': '导出班级成绩',
    'organization.read': '查看班级组织', 'reviewers.manage': '管理本班审查员',
    'admin.users': '账号与导入管理', 'admin.roles': '权限矩阵管理', 'admin.classes': '组织和身份授权',
}


def initialize():
    with db.write() as conn:
        if db.rows(conn, 'users'):
            return
        digest = password_hash(DEMO_PASSWORD)
        admin = db.add(conn, 'users', username='admin', display_name='系统管理员',
                       base_identity='ADMIN', password_hash=digest)
        counselor = db.add(conn, 'users', username='counselor', display_name='周老师',
                           base_identity='COUNSELOR', password_hash=digest)
        role_defs = [('STUDENT', '学生', 'STUDENT', 'GLOBAL'), ('MONITOR', '班长', 'STUDENT', 'CLASS'),
                     ('REVIEWER', '学生审查员', 'STUDENT', 'CLASS'),
                     ('COUNSELOR', '辅导员', 'COUNSELOR', 'GLOBAL'), ('ADMIN', '系统管理员', 'ADMIN', 'GLOBAL')]
        roles = {code: db.add(conn, 'roles', code=code, name=name, allowed_identity=identity,
                              scope_kind=scope, is_system=True, description='受强制业务策略及班级作用域约束')
                 for code, name, identity, scope in role_defs}
        permissions = {code: db.add(conn, 'permissions', code=code, name=name,
                                    module=code.split('.')[0], action=code.split('.')[1], is_system=True,
                                    boundary_note='权限与授权范围相交；不允许绕过基础身份、自审、公示及材料隐私约束')
                       for code, name in PERMISSIONS.items()}
        rules = {
            'STUDENT': {p: 'OWN' for p in ('application.read', 'application.write', 'application.submit',
                                          'application.withdraw', 'materials.read', 'scores.read')}
                       | {'activity.read': 'SYSTEM_METADATA', 'publicity.read': 'CLASS_PUBLIC', 'organization.read': 'OWN'},
            'MONITOR': {p: 'CLASS' for p in ('application.read', 'materials.read', 'scores.read',
                                            'export.read', 'reviewers.manage', 'organization.read')},
            'REVIEWER': {p: 'CLASS' for p in ('application.read', 'materials.read', 'scores.read',
                                             'review.peer', 'organization.read')},
            'COUNSELOR': {p: 'LED_CLASSES' for p in ('application.read', 'materials.read', 'scores.read',
                                                    'review.counselor', 'review.revoke', 'publicity.manage',
                                                    'publicity.read', 'export.read', 'organization.read')}
                          | {'activity.read': 'SYSTEM_METADATA', 'activity.manage': 'SYSTEM_METADATA'},
            'ADMIN': {p: 'SYSTEM_METADATA' for p in ('admin.users', 'admin.roles', 'admin.classes', 'organization.read')},
        }
        for rolecode, role in roles.items():
            for code, perm in permissions.items():
                scope = rules[rolecode].get(code, 'OWN')
                db.add(conn, 'role_permissions', role_id=role['id'], permission_id=perm['id'],
                       data_scope=scope, is_allowed=code in rules[rolecode], changed_by=admin['id'])
        for user, rolecode in [(admin, 'ADMIN'), (counselor, 'COUNSELOR')]:
            db.add(conn, 'role_assignments', user_id=user['id'], role_id=roles[rolecode]['id'], granted_by=admin['id'])
        text = (db.ROOT / 'docs/scoring-rules.md').read_text(encoding='utf-8')
        rule = db.add(conn, 'score_rule_versions', version_label='西交2018+已确认项目规则 V1', baseline_score=70,
                      rounding_mode='HALF_UP', source_document='附件1 西安交通大学本科生综合素质测评成绩评定办法.pdf',
                      rules_json={'reference_text': text, 'volunteer_annual_cap': 1, 'volunteer_hours': 32,
                                  'collective_each': .3, 'no_deductions': True},
                      published_by=admin['id'], status='PUBLISHED')
        for i, (code, name, cap, aggregation, counselor_review) in enumerate(CATEGORIES):
            db.add(conn, 'score_categories', code=code, name=name, default_cap=cap,
                   aggregation_kind=aggregation, requires_eligibility_review=counselor_review, sort_order=i)
        years = [db.add(conn, 'academic_years', code=code, name=f'{code}学年', sort_order=i, is_selectable=True)
                 for i, code in enumerate(['2025-2026', '2026-2027'])]
        class_names = ['软件2401', '软件2402'] + ['信息240' + str(i) for i in range(1, 7)]
        for index, name in enumerate(class_names):
            classroom = db.add(conn, 'classes', name=name, code=f'DEMO-{index+1:02d}', cohort_year=2024,
                               program_name='软件工程' if index < 2 else '信息工程')
            db.add(conn, 'counselor_classes', counselor_id=counselor['id'], class_id=classroom['id'], assigned_by=admin['id'])
            cys = []
            for year in years:
                cy = db.add(conn, 'class_years', class_id=classroom['id'], academic_year_id=year['id'],
                            score_rule_version_id=rule['id'])
                db.add(conn, 'class_publicity', class_year_id=cy['id'], is_open=False)
                cys.append(cy)
            people = [('monitor', '张知远'), ('reviewer1', '陈思齐'), ('reviewer2', '李沐阳'),
                      ('student', '王欣然'), ('student2', '赵一诺')] if index == 0 else [
                          (f'class{index+1}_monitor', name+'班长'),
                          (f'class{index+1}_reviewer1', name+'审查员甲'),
                          (f'class{index+1}_reviewer2', name+'审查员乙')]
            for j, (username, display_name) in enumerate(people):
                user = db.add(conn, 'users', username=username, display_name=display_name,
                              base_identity='STUDENT', password_hash=digest)
                student = db.add(conn, 'student_profiles', user_id=user['id'],
                                 student_number=f'DEMO2024{index+1:02d}{j+1:03d}', admission_year=2024,
                                 profile_status='ACTIVE')
                db.add(conn, 'role_assignments', user_id=user['id'], role_id=roles['STUDENT']['id'], granted_by=admin['id'])
                if j < 3:
                    db.add(conn, 'role_assignments', user_id=user['id'],
                           role_id=roles['MONITOR' if j == 0 else 'REVIEWER']['id'],
                           class_id=classroom['id'], granted_by=admin['id'])
                for cy in cys:
                    db.add(conn, 'enrollments', student_id=student['id'], class_year_id=cy['id'],
                           managed_by=admin['id'])

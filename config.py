# SheerID 验证配置文件

import os

# SheerID API 配置
PROGRAM_ID = '67c8c14f5f17a83b745e3f82'
SHEERID_BASE_URL = 'https://services.sheerid.com'
MY_SHEERID_URL = 'https://my.sheerid.com'

# 验证页链接模板（你提供的链接同款，只是 verificationId 会动态替换）
VERIFY_URL_TEMPLATE = f"{SHEERID_BASE_URL}/verify/{PROGRAM_ID}/?verificationId={{verification_id}}"

# 文件大小限制
MAX_FILE_SIZE = 1 * 1024 * 1024  # 1MB

# 学校配置
SCHOOLS = {
    '110565': {
        'id': 110565,
        'idExtended': '110565',
        'name': 'California State University-Fullerton',
        'city': 'Fullerton',
        'state': 'CA',
        'country': 'US',
        'type': 'UNIVERSITY',
        'domain': 'FULLERTON.EDU',
        'latitude': 33.882348,
        'longitude': -117.885104
    }
}

# 默认学校（可通过环境变量 ONE_DEFAULT_SCHOOL_ID 覆盖）
DEFAULT_SCHOOL_ID = (os.getenv('ONE_DEFAULT_SCHOOL_ID', '110565') or '110565').strip()

# UTM 参数（营销追踪参数）
# 如果 URL 中没有这些参数，会自动添加
DEFAULT_UTM_PARAMS = {
    'utm_source': 'gemini',
    'utm_medium': 'paid_media',
    'utm_campaign': 'students_pmax_bts-slap'
}




def get_school(school_id: str):
    """按 school_id 获取学校配置；不存在时回退 DEFAULT_SCHOOL_ID，再回退 110565。"""
    sid = str(school_id or DEFAULT_SCHOOL_ID).strip()
    return SCHOOLS.get(sid) or SCHOOLS.get(DEFAULT_SCHOOL_ID) or SCHOOLS.get("110565")


def get_school_domain(school_id: str) -> str:
    school = get_school(school_id) or {}
    return str(school.get("domain", "FULLERTON.EDU"))

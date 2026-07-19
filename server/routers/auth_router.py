from fastapi import APIRouter, Depends, HTTPException, Request, status, Response
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import secrets

from server.db_manager import db_manager
from server.models.user_model import User, OperationLog
from server.models.cas_session_model import CASSession
from server.utils.auth_utils import AuthUtils
from server.utils.auth_middleware import get_db, get_current_user, get_admin_user, get_superadmin_user, oauth2_scheme
from server.utils.cas_utils import CASUtils
from server.utils.cas_cleanup import cleanup_expired_cas_sessions, get_cas_session_stats

import logging
import os

# 环境配置
IS_PRODUCTION = os.getenv("ENVIRONMENT") == "production"

# 设置日志文件路径，当前目录下的 debug.log
log_file_path = os.path.join(os.path.dirname(__file__), "debug.log")

# 配置 logging
logging.basicConfig(
    level=logging.DEBUG,  # 或改为 logging.INFO，根据需求
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()  # 可选，打印到控制台
    ]
)
logger = logging.getLogger(__name__)
# 日志文件用法示例：
# logger.debug(f"尝试删除用户 ID: {user_id}，当前操作用户: {current_user.username} (ID: {current_user.id})")


# 创建路由器
auth = APIRouter(prefix="/auth", tags=["auth"])

# 请求和响应模型
class Token(BaseModel):
    access_token: str
    token_type: str
    user_id: int
    username: str
    role: str

class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"

class UserUpdate(BaseModel):
    username: str | None = None
    password: str | None = None
    role: str | None = None

class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    created_at: str
    last_login: str | None = None
    is_cas_user: bool
    email: str | None = None
    display_name: str | None = None
    department: str | None = None

class InitializeAdmin(BaseModel):
    username: str
    password: str

class CASCallback(BaseModel):
    ticket: str

# 记录操作日志
def log_operation(db: Session, user_id: int, operation: str, details: str = None, request: Request = None):
    ip_address = None
    if request:
        ip_address = request.client.host if request.client else None

    log = OperationLog(
        user_id=user_id,
        operation=operation,
        details=details,
        ip_address=ip_address
    )
    db.add(log)
    db.commit()

# 路由：登录获取令牌
@auth.post("/token", response_model=Token)
async def login_for_access_token(
    # 自动解析请求中的用户名密码表单无需手动解析
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    # 查找用户
    user = db.query(User).filter(User.username == form_data.username).first()

    # 验证用户存在且密码正确
    if not user or not AuthUtils.verify_password(user.password_hash, form_data.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 检查是否为CAS用户（CAS用户不能使用密码登录）
    if user.is_cas_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="在校老师或学生请使用统一身份认证登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 更新最后登录时间
    user.last_login = datetime.now()
    db.commit()

    # 生成访问令牌
    token_data = {"sub": str(user.id)}
    access_token = AuthUtils.create_access_token(token_data)

    # 记录登录操作
    log_operation(db, user.id, "登录")

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username,
        "role": user.role
    }

# 路由：校验是否需要初始化管理员
@auth.get("/check-first-run")
async def check_first_run():
    is_first_run = db_manager.check_first_run()
    return {"first_run": is_first_run}

# 路由：初始化管理员账户
@auth.post("/initialize", response_model=Token)
async def initialize_admin(
    admin_data: InitializeAdmin,
    db: Session = Depends(get_db)
):
    # 检查是否是首次运行
    if not db_manager.check_first_run():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="系统已经初始化，无法再次创建初始管理员",
        )

    # 创建管理员账户
    hashed_password = AuthUtils.hash_password(admin_data.password)

    new_admin = User(
        username=admin_data.username,
        password_hash=hashed_password,
        role="superadmin",
        last_login=datetime.now()
    )

    db.add(new_admin)
    db.commit()
    db.refresh(new_admin)

    # 生成访问令牌
    token_data = {"sub": str(new_admin.id)}
    access_token = AuthUtils.create_access_token(token_data)

    # 记录操作
    log_operation(db, new_admin.id, "系统初始化", "创建超级管理员账户")

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": new_admin.id,
        "username": new_admin.username,
        "role": new_admin.role
    }

# 路由：获取当前用户信息
@auth.get("/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user.to_dict()

# CAS认证相关路由
@auth.get("/cas/login")
async def cas_login(request: Request):
    """发起CAS登录"""
    cas_utils = CASUtils()
    login_url = cas_utils.get_login_url()
    
    # 记录重定向到CAS
    logger.info(f"重定向到CAS登录: {login_url}")
    
    return {"login_url": login_url}

from fastapi import Query

@auth.get("/cas/callback")
async def cas_callback(
    request: Request,
    ticket: str = Query(..., description="CAS ticket"),
    db: Session = Depends(get_db)
):
    """CAS回调处理 - 使用HTTP Only Cookie安全方案"""
    cas_utils = CASUtils()
    
    # 验证CAS ticket
    success, user_info, error_message = cas_utils.validate_ticket(ticket)
    
    if not success:
        logger.error(f"CAS ticket验证失败: {error_message}")
        # 重定向到登录页面并显示错误信息
        error_url = f"/login?error=CAS认证失败: {error_message}"
        return RedirectResponse(url=error_url, status_code=302)
    
    # 查找或创建用户
    user = await _find_or_create_cas_user(db, user_info)
    
    # 更新最后登录时间
    user.last_login = datetime.now()
    db.commit()
    
    # 生成访问令牌
    token_data = {"sub": str(user.id)}
    access_token = AuthUtils.create_access_token(token_data)
    
    # 生成一次性session token用于前端验证
    session_token = secrets.token_urlsafe(32)
    
    # 存储session token到专门的CAS Session表（临时存储，5分钟过期）
    session_expiry = datetime.now() + timedelta(minutes=5)
    
    # 创建CAS Session记录
    cas_session = CASSession(
        session_token=session_token,
        user_id=user.id,
        access_token=access_token,
        expires_at=session_expiry
    )
    db.add(cas_session)
    db.commit()
    
    # 记录登录操作
    log_operation(
        db,
        user.id,
        "CAS登录",
        f"CAS用户登录: {user.username}",
        request
    )
    
    logger.info(f"CAS用户登录成功: {user.username}")
    
    # 创建重定向响应并在上面设置Cookie
    frontend_url = f"/login?session_token={session_token}"
    redirect_response = RedirectResponse(url=frontend_url, status_code=302)
    
    # 在重定向响应上设置HTTP Only Cookie
    redirect_response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,      # 防止XSS攻击
        secure=IS_PRODUCTION,  # 生产环境启用HTTPS
        samesite="strict" if IS_PRODUCTION else "lax",  # 生产环境使用strict
        max_age=24*3600,       # 1小时过期，与JWT保持一致
        path="/"            # 对整个站点有效
    )
    
    return redirect_response

async def _find_or_create_cas_user(db: Session, user_info: dict) -> User:
    """查找或创建CAS用户"""
    # 首先通过CAS用户名查找
    user = db.query(User).filter(User.username == user_info["username"]).first()
    
    if user:
        # 更新用户信息
        # 只在明确提供role且不为空时更新role，避免意外降级或升级
        if user_info.get("role"):
            user.role = user_info.get("role", user.role)
            user.role = "admin"
        return user
    
    new_user = User(
        username=user_info["username"],
        # 新创建的CAS用户默认最小权限为'user'
        role=user_info.get("role", "admin"),
        is_cas_user=True,
        last_login=datetime.now()
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    logger.info(f"创建新的CAS用户: {new_user.username}")
    return new_user

@auth.get("/cas/logout")
async def cas_logout(current_user: User = Depends(get_current_user)):
    """获取CAS登出URL"""
    cas_utils = CASUtils()
    logout_url = cas_utils.get_logout_url()
    
    return {"logout_url": logout_url}

@auth.get("/cas/exchange")
async def exchange_session_token(
    session_token: str = Query(..., description="一次性session token"),
    db: Session = Depends(get_db)
):
    """验证session token并返回用户信息"""
    # 查找有效的session token记录
    cas_session = db.query(CASSession).filter(
        CASSession.session_token == session_token,
        CASSession.expires_at > datetime.now()
    ).first()
    
    if not cas_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或过期的session token",
        )
    
    # 获取用户信息
    user = db.query(User).filter(User.id == cas_session.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )
    
    # 删除已使用的session token（一次性使用）
    db.delete(cas_session)
    db.commit()
    
    logger.info(f"Session token验证成功，用户: {user.username}")
    
    # 同时返回access_token，便于前端将其保存在内存或localStorage中（考虑改为HttpOnly Cookie以提高安全性）
    token_data = {"sub": str(user.id)}
    access_token = AuthUtils.create_access_token(token_data)

    return {
        "access_token": access_token,
        "user_id": user.id,
        "username": user.username,
        "role": user.role,
        "is_cas_user": user.is_cas_user
    }

# CAS Session管理接口（管理员权限）
@auth.post("/cas/cleanup")
async def cleanup_cas_sessions(
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """清理过期的CAS Session记录"""
    deleted_count = cleanup_expired_cas_sessions(db)
    
    return {
        "success": True,
        "message": f"清理了 {deleted_count} 个过期的CAS Session记录",
        "deleted_count": deleted_count
    }

@auth.get("/cas/stats")
async def get_cas_session_stats(
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """获取CAS Session统计信息"""
    stats = get_cas_session_stats(db)
    
    return {
        "success": True,
        "stats": stats
    }

# 路由：创建新用户（管理员权限）
@auth.post("/users", response_model=UserResponse)
async def create_user(
    user_data: UserCreate,
    request: Request,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    # 检查用户名是否已存在
    existing_user = db.query(User).filter(User.username == user_data.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已存在",
        )

    # 创建新用户
    hashed_password = AuthUtils.hash_password(user_data.password)

    # 检查角色权限
    # 超级管理员可以创建任何类型的用户
    if user_data.role == "superadmin" and current_user.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有超级管理员才能创建超级管理员账户",
        )

    # 管理员只能创建普通用户
    if current_user.role == "admin" and user_data.role != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理员只能创建普通用户账户",
        )

    new_user = User(
        username=user_data.username,
        password_hash=hashed_password,
        role=user_data.role
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # 记录操作
    log_operation(
        db,
        current_user.id,
        "创建用户",
        f"创建用户: {user_data.username}, 角色: {user_data.role}",
        request
    )

    return new_user.to_dict()

# 路由：获取所有用户（管理员权限）
@auth.get("/users", response_model=list[UserResponse])
async def read_users(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    users = db.query(User).offset(skip).limit(limit).all()
    return [user.to_dict() for user in users]

# 路由：获取特定用户信息（管理员权限）
@auth.get("/users/{user_id}", response_model=UserResponse)
async def read_user(
    user_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )
    return user.to_dict()

# 路由：更新用户信息（管理员权限）
@auth.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    request: Request,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    # 检查权限
    if user.role == "superadmin" and current_user.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有超级管理员才能修改超级管理员账户",
        )

    # 超级管理员账户不能被降级（只能由其他超级管理员修改）
    if user.role == "superadmin" and user_data.role and user_data.role != "superadmin" and current_user.id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="不能降级超级管理员账户",
        )

    # 更新信息
    update_details = []

    if user_data.username is not None:
        # 检查用户名是否已被其他用户使用
        existing_user = db.query(User).filter(User.username == user_data.username, User.id != user_id).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="用户名已存在",
            )
        user.username = user_data.username
        update_details.append(f"用户名: {user_data.username}")

    if user_data.password is not None:
        user.password_hash = AuthUtils.hash_password(user_data.password)
        update_details.append("密码已更新")

    if user_data.role is not None:
        user.role = user_data.role
        update_details.append(f"角色: {user_data.role}")

    db.commit()

    # 记录操作
    log_operation(
        db,
        current_user.id,
        "更新用户",
        f"更新用户ID {user_id}: {', '.join(update_details)}",
        request
    )

    return user.to_dict()

# 路由：删除用户（管理员权限）
@auth.delete("/users/{user_id}", response_model=dict)
async def delete_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    # 检查权限
    if user.role == "superadmin":
        # 只有超级管理员可以删除超级管理员
        if current_user.role != "superadmin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有超级管理员才能删除超级管理员账户",
            )

        # 检查是否是最后一个超级管理员
        superadmin_count = db.query(User).filter(User.role == "superadmin").count()
        if superadmin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="不能删除最后一个超级管理员账户",
            )

    # 不能删除自己的账户
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能删除自己的账户",
        )

    # 记录操作
    log_operation(
        db,
        current_user.id,
        "删除用户",
        f"删除用户: {user.username}, ID: {user.id}, 角色: {user.role}",
        request
    )

    # 删除用户
    db.delete(user)
    db.commit()

    return {"success": True, "message": "用户已删除"}

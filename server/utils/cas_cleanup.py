import logging
from datetime import datetime
from sqlalchemy.orm import Session
from server.models.cas_session_model import CASSession

logger = logging.getLogger(__name__)

def cleanup_expired_cas_sessions(db: Session) -> int:
    """
    清理过期的CAS Session记录
    
    Args:
        db: 数据库会话
        
    Returns:
        int: 删除的记录数量
    """
    try:
        # 查找所有过期的session记录
        expired_sessions = db.query(CASSession).filter(
            CASSession.expires_at < datetime.now()
        ).all()
        
        deleted_count = len(expired_sessions)
        
        if deleted_count > 0:
            # 批量删除过期记录
            for session in expired_sessions:
                db.delete(session)
            
            db.commit()
            logger.info(f"清理了 {deleted_count} 个过期的CAS Session记录")
        else:
            logger.debug("没有找到过期的CAS Session记录")
        
        return deleted_count
        
    except Exception as e:
        logger.error(f"清理CAS Session记录时发生错误: {e}")
        db.rollback()
        return 0

def get_cas_session_stats(db: Session) -> dict:
    """
    获取CAS Session统计信息
    
    Args:
        db: 数据库会话
        
    Returns:
        dict: 统计信息
    """
    try:
        total_sessions = db.query(CASSession).count()
        active_sessions = db.query(CASSession).filter(
            CASSession.expires_at > datetime.now()
        ).count()
        expired_sessions = total_sessions - active_sessions
        
        return {
            "total_sessions": total_sessions,
            "active_sessions": active_sessions,
            "expired_sessions": expired_sessions,
            "cleanup_timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"获取CAS Session统计信息时发生错误: {e}")
        return {
            "total_sessions": 0,
            "active_sessions": 0,
            "expired_sessions": 0,
            "error": str(e)
        }

import os
import urllib.parse
import requests
from typing import Dict, Optional, Tuple
import logging
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

class CASUtils:
    """CAS认证工具类"""
    
    def __init__(self):
        # CAS服务器配置
        self.cas_server_url = "http://10.18.19.47:8002/cas"
        self.service_url ="https://chat.cup.edu.cn"
        self.validate_url = f"{self.cas_server_url}/serviceValidate"
        
        # 用户属性映射配置
        self.attribute_mapping = {
            "username": os.environ.get("CAS_ATTR_USERNAME", "user"),
            "email": os.environ.get("CAS_ATTR_EMAIL", "mail"),
            "display_name": os.environ.get("CAS_ATTR_DISPLAY_NAME", "displayName"),
            "department": os.environ.get("CAS_ATTR_DEPARTMENT", "department"),
            "roles": os.environ.get("CAS_ATTR_ROLES", "roles")
        }
        
        # 角色映射配置
        self.role_mapping = {
            "admin": os.environ.get("CAS_ROLE_ADMIN", "admin"),
            "superadmin": os.environ.get("CAS_ROLE_SUPERADMIN", "superadmin"),
            "user": os.environ.get("CAS_ROLE_USER", "user")
        }
    
    def get_login_url(self, service: Optional[str] = None) -> str:
        """获取CAS登录URL"""
        if not service:
            service = f"{self.service_url}/api/auth/cas/callback"
        
        login_url = f"{self.cas_server_url}/login"
        params = {
            "service": service
        }
        
        return f"{login_url}?{urllib.parse.urlencode(params)}"
    
    def validate_ticket(self, ticket: str, service: Optional[str] = None) -> Tuple[bool, Optional[Dict], Optional[str]]:
        """验证CAS ticket并获取用户信息"""
        if not service:
            service = f"{self.service_url}/api/auth/cas/callback"
        
        try:
            # 构建验证URL
            params = {
                "ticket": ticket,
                "service": service,
                "format": "XML"  # 使用XML格式获取更多属性
            }
            
            validate_url = f"{self.validate_url}?{urllib.parse.urlencode(params)}"
            
            # 发送验证请求
            response = requests.get(validate_url, timeout=10)
            response.raise_for_status()
            
            # 解析XML响应
            return self._parse_cas_response(response.text)
            
        except requests.RequestException as e:
            logger.error(f"CAS ticket验证请求失败: {e}")
            return False, None, "CAS服务器连接失败"
        except Exception as e:
            logger.error(f"CAS ticket验证解析失败: {e}")
            return False, None, "CAS响应解析失败"
    
    def _parse_cas_response(self, xml_response: str) -> Tuple[bool, Optional[Dict], Optional[str]]:
        """解析CAS XML响应"""
        try:
            root = ElementTree.fromstring(xml_response)
            
            # 检查认证是否成功
            authentication_success = root.find(".//{http://www.yale.edu/tp/cas}authenticationSuccess")
            if authentication_success is None:
                # 认证失败
                failure_element = root.find(".//{http://www.yale.edu/tp/cas}authenticationFailure")
                if failure_element is not None:
                    error_code = failure_element.get("code", "UNKNOWN_ERROR")
                    error_message = failure_element.text or "认证失败"
                    return False, None, f"{error_code}: {error_message}"
                return False, None, "CAS认证失败"
            
            # 提取用户信息
            user_element = authentication_success.find(".//{http://www.yale.edu/tp/cas}user")
            if user_element is None:
                return False, None, "未找到用户信息"
            
            username = user_element.text
            if not username:
                return False, None, "用户名为空"
            
            # 提取用户属性
            attributes = {}
            attributes_element = authentication_success.find(".//{http://www.yale.edu/tp/cas}attributes")
            if attributes_element is not None:
                for attr in attributes_element:
                    tag = attr.tag.split("}")[-1] if "}" in attr.tag else attr.tag
                    attributes[tag] = attr.text
            
            # 构建用户信息
            user_info = {
                "username": username,
                "email": attributes.get(self.attribute_mapping["email"], ""),
                "display_name": attributes.get(self.attribute_mapping["display_name"], username),
                "department": attributes.get(self.attribute_mapping["department"], ""),
                "raw_attributes": attributes
            }
            
            # # 获取认证中心返回的用户信息
            # cas_attributes = request.session.get('attributes', {})
            # student_id = cas_attributes.get('employeeNumber')
            # username = cas_attributes.get('name')

            # 映射角色
            user_info["role"] = self._map_user_role(attributes)
            
            logger.info(f"CAS认证成功: 用户 {user_info}, 角色 {user_info['role']}")
            logger.info(f"CAS认证信息: 用户 {user_element.text}, 属性 {attributes}")
            return True, user_info, None
            
        except ElementTree.ParseError as e:
            logger.error(f"CAS XML解析错误: {e}")
            return False, None, "CAS响应格式错误"
        except Exception as e:
            logger.error(f"CAS响应处理异常: {e}")
            return False, None, "CAS响应处理失败"
    
    def _map_user_role(self, attributes: Dict) -> str:
        """根据CAS属性映射用户角色"""
        # 从属性中获取角色信息
        roles_attr = attributes.get(self.attribute_mapping["roles"], "")
        if isinstance(roles_attr, str):
            roles = [role.strip() for role in roles_attr.split(",") if role.strip()]
        else:
            roles = []
        
        # 检查角色映射
        for cas_role, local_role in self.role_mapping.items():
            if cas_role in roles or any(cas_role in role for role in roles):
                return local_role
        
        # 默认角色
        return "user"
    
    def get_logout_url(self, service: Optional[str] = None) -> str:
        """获取CAS登出URL"""
        logout_url = f"{self.cas_server_url}/logout"
        if not service:
            service = f"{self.service_url}/login"
        params = {"service": service}
        return f"{logout_url}?{urllib.parse.urlencode(params)}"

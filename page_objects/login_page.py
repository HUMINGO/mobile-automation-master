from .base import PageElement,BasePage

class LoginPage(BasePage):
    phone_num_input = PageElement("手机号输入框", text="Phone number")
    next_button = PageElement("下一步按钮", content_desc="Next")
    verification_prompt = PageElement(
        "验证码页面提示", text="Verification code has been sent to +91-13686868866"
    )

import { FormEvent, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { useAuthMotion } from "../animations/pageMotion";
import logoUrl from "../assets/logo.png";
import { BRAND_LOGO_ALT, BRAND_NAME } from "../config/brand";
import { useAuth } from "../features/auth/AuthContext";

export function AuthPage({ mode }: { mode: "login" | "register" }) {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordFocused, setPasswordFocused] = useState(false);
  const [confirmPasswordTouched, setConfirmPasswordTouched] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const cardRef = useRef<HTMLElement | null>(null);
  const isRegister = mode === "register";
  useAuthMotion(cardRef);

  const redirectPath = useMemo(() => {
    const params = new URLSearchParams(location.search);
    const redirect = params.get("redirect");
    return redirect?.startsWith("/") ? redirect : "/";
  }, [location.search]);

  const passwordRules = useMemo(() => getPasswordRuleStates(password), [password]);
  const showPasswordRequirements = isRegister && (passwordFocused || password.length > 0);
  const confirmPasswordMessage = isRegister && confirmPasswordTouched
    ? getRegisterInlineMessage(password, confirmPassword)
    : null;
  const registerSubmitDisabled = isRegister
    ? isRegisterSubmitDisabled(username, password, confirmPassword, auth.loading)
    : auth.loading;

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setErrorMessage(null);

    const credentials = {
      username: username.trim(),
      password
    };

    if (!credentials.username || !credentials.password) {
      setErrorMessage("请输入用户名和密码");
      return;
    }
    if (isRegister) {
      const passwordError = validateRegisterPassword(password, confirmPassword);
      if (passwordError) {
        setErrorMessage(passwordError);
        return;
      }
    }

    try {
      if (isRegister) {
        await auth.register({ ...credentials, confirmPassword });
      } else {
        await auth.login(credentials);
      }
      navigate(redirectPath, { replace: true });
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : "认证失败，请检查输入");
    }
  }

  return (
    <main className="auth-shell">
      <section ref={cardRef} className="auth-card">
        <div className="auth-brand">
          <img className="brand-logo auth-logo" src={logoUrl} alt={BRAND_LOGO_ALT} />
          <div>
            <p className="eyebrow">{BRAND_NAME}</p>
            <h1>{isRegister ? "注册账号" : "登录系统"}</h1>
          </div>
        </div>

        <p className="auth-description">
          {isRegister ? "注册后即可创建公开或私有评测。" : "登录后继续查看评测任务与模型结果。"}
        </p>

        <form className="auth-form" onSubmit={(event) => void handleSubmit(event)}>
          <label>
            用户名
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              maxLength={64}
            />
          </label>
          <label>
            密码
            <input
              value={password}
              onBlur={() => setPasswordFocused(false)}
              onChange={(event) => {
                setPassword(event.target.value);
                setErrorMessage(null);
              }}
              onFocus={() => setPasswordFocused(true)}
              type="password"
              autoComplete={isRegister ? "new-password" : "current-password"}
              maxLength={128}
            />
          </label>
          {showPasswordRequirements ? (
            <div className="password-rules" aria-live="polite">
              <p>密码需要满足以下条件</p>
              <ul>
                {passwordRules.map((rule) => (
                  <li className={rule.valid ? "valid" : "invalid"} key={rule.key}>
                    <span aria-hidden="true">{rule.valid ? "✓" : "•"}</span>
                    {rule.label}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {isRegister ? (
            <label>
              确认密码
              <input
                value={confirmPassword}
                aria-invalid={confirmPasswordMessage ? "true" : "false"}
                onBlur={() => setConfirmPasswordTouched(true)}
                onChange={(event) => {
                  setConfirmPassword(event.target.value);
                  setConfirmPasswordTouched(true);
                  setErrorMessage(null);
                }}
                type="password"
                autoComplete="new-password"
                maxLength={128}
              />
              {confirmPasswordMessage ? (
                <span className="field-hint error" role="alert">{confirmPasswordMessage}</span>
              ) : confirmPassword ? (
                <span className="field-hint success">两次输入的密码一致</span>
              ) : null}
            </label>
          ) : null}
          {errorMessage ? <p className="error-message">{errorMessage}</p> : null}
          <button className="auth-submit" type="submit" disabled={registerSubmitDisabled}>
            {auth.loading ? "处理中..." : isRegister ? "注册并登录" : "登录"}
          </button>
        </form>

        <p className="auth-switch">
          {isRegister ? "已有账号？" : "还没有账号？"}
          <Link to={isRegister ? "/login" : "/register"}>{isRegister ? "返回登录" : "立即注册"}</Link>
        </p>
      </section>
    </main>
  );
}

function validateRegisterPassword(password: string, confirmPassword: string): string | null {
  if (password.length < 8) {
    return "密码长度不能少于 8 位";
  }
  if (!hasDigit(password) || !hasLowercase(password) || !hasUppercase(password)) {
    return "密码必须包含数字、小写字母和大写字母";
  }
  if (!confirmPassword) {
    return "请再次输入密码";
  }
  if (password !== confirmPassword) {
    return "两次输入的密码不一致";
  }
  return null;
}

type PasswordRuleKey = "length" | "digit" | "lowercase" | "uppercase";

export interface PasswordRuleState {
  key: PasswordRuleKey;
  label: string;
  valid: boolean;
}

export function getPasswordRuleStates(password: string): PasswordRuleState[] {
  return [
    { key: "length", label: "至少 8 位", valid: isPasswordStrong(password) },
    { key: "digit", label: "包含数字", valid: hasDigit(password) },
    { key: "lowercase", label: "包含小写字母", valid: hasLowercase(password) },
    { key: "uppercase", label: "包含大写字母", valid: hasUppercase(password) }
  ];
}

export function getRegisterInlineMessage(password: string, confirmPassword: string): string | null {
  if (!confirmPassword) {
    return null;
  }
  return password === confirmPassword ? null : "两次输入的密码不一致";
}

export function isRegisterSubmitDisabled(
  username: string,
  password: string,
  confirmPassword: string,
  loading: boolean
): boolean {
  return loading || !username.trim() || !isPasswordStrong(password) || !confirmPassword || password !== confirmPassword;
}

function isPasswordStrong(password: string): boolean {
  return password.length >= 8 && hasDigit(password) && hasLowercase(password) && hasUppercase(password);
}

function hasDigit(password: string): boolean {
  return /\d/.test(password);
}

function hasLowercase(password: string): boolean {
  return /[a-z]/.test(password);
}

function hasUppercase(password: string): boolean {
  return /[A-Z]/.test(password);
}

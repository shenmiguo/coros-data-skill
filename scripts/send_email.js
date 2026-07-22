import "dotenv/config";
import nodemailer from "nodemailer";

/**
 * 通过 QQ 邮箱 SMTP 发送训练简报
 *
 * 环境变量 (.env):
 *   QQ_MAIL_USER=<QQ邮箱地址，如 xxx@qq.com>
 *   QQ_MAIL_AUTH_CODE=<QQ邮箱授权码（非登录密码）>
 *
 * 授权码获取: QQ邮箱 → 设置 → 账户 → POP3/SMTP服务 → 开启 → 生成授权码
 */

function createTransporter() {
  const user = process.env.QQ_MAIL_USER;
  const pass = process.env.QQ_MAIL_AUTH_CODE;
  if (!user || !pass) {
    throw new Error("缺少 QQ 邮箱配置: 请在 .env 中设置 QQ_MAIL_USER 和 QQ_MAIL_AUTH_CODE");
  }
  return nodemailer.createTransport({
    host: "smtp.qq.com",
    port: 465,
    secure: true,
    auth: { user, pass },
  });
}

/**
 * 发送邮件
 * @param {object} opts
 * @param {string} opts.to - 收件人邮箱
 * @param {string} opts.subject - 邮件标题
 * @param {string} opts.text - 纯文本内容
 * @param {string} [opts.html] - HTML 内容（可选，优先于 text）
 */
export async function sendMail({ to, subject, text, html }) {
  const transporter = createTransporter();
  const user = process.env.QQ_MAIL_USER;
  const info = await transporter.sendMail({
    from: `"COROS 教练" <${user}>`,
    to,
    subject,
    text,
    html: html || text,
  });
  return info.messageId || info.response;
}

/**
 * 发送训练简报邮件
 * @param {string} to - 收件人
 * @param {string} subject - 标题
 * @param {string} content - 简报内容（Markdown 或纯文本）
 */
export async function sendBriefingEmail(to, subject, content) {
  // 简单的 Markdown -> HTML 转换
  const html = content
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1>$1</h1>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, "<code>$1</code>")
    .replace(/\n/g, "<br>\n");

  const wrapped = `<div style="font-family: -apple-system, 'PingFang SC', sans-serif; font-size: 14px; line-height: 1.8; max-width: 680px; margin: 0 auto; padding: 20px;">${html}</div>`;

  return sendMail({ to, subject, text: content, html: wrapped });
}

// CLI 直接运行: node send_email.js "收件人" "标题" "内容"
if (process.argv[1] && process.argv[1].endsWith("send_email.js")) {
  const [to, subject, content] = process.argv.slice(2);
  if (!to || !subject || !content) {
    console.error("用法: node send_email.js <收件人> <标题> <内容>");
    process.exit(1);
  }
  sendBriefingEmail(to, subject, content)
    .then((id) => console.log("✅ 邮件发送成功:", id))
    .catch((e) => {
      console.error("❌ 邮件发送失败:", e.message);
      process.exit(1);
    });
}

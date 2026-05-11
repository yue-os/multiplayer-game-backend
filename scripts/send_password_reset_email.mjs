import nodemailer from 'nodemailer'

const [recipientEmail, resetLink] = process.argv.slice(2)

if (!recipientEmail || !resetLink) {
  console.error('recipientEmail and resetLink are required')
  process.exit(1)
}

const smtpUser = process.env.SMTP_EMAIL || process.env.GMAIL_SMTP_USER || process.env.SMTP_GMAIL_USER
const smtpPass = process.env.SMTP_PASSWORD || process.env.GMAIL_SMTP_APP_PASSWORD || process.env.SMTP_GMAIL_APP_PASSWORD

if (!smtpUser || !smtpPass) {
  console.error('Gmail SMTP credentials are not configured. Expected SMTP_EMAIL/SMTP_PASSWORD or GMAIL_SMTP_USER/GMAIL_SMTP_APP_PASSWORD.')
  process.exit(1)
}

const transporter = nodemailer.createTransport({
  service: 'gmail',
  auth: {
    user: smtpUser,
    pass: smtpPass,
  },
})

await transporter.sendMail({
  from: `"BatangAware Security" <${smtpUser}>`,
  to: recipientEmail,
  subject: 'Password reset approved',
  text: [
    'Your password reset request was approved.',
    '',
    'Open this secure link within 30 minutes to set a new password:',
    resetLink,
    '',
    'If you did not request this, please contact your administrator immediately.',
  ].join('\n'),
  html: `
    <div style="font-family:Arial,sans-serif;line-height:1.5;color:#172033">
      <h2>Password reset approved</h2>
      <p>Your password reset request was approved.</p>
      <p><a href="${resetLink}" style="display:inline-block;padding:10px 14px;background:#4DB6AC;color:#fff;text-decoration:none;border-radius:8px">Reset password</a></p>
      <p>This secure link expires in 30 minutes.</p>
      <p>If you did not request this, contact your administrator immediately.</p>
    </div>
  `,
})

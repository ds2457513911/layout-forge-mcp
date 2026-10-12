# skill-syntax —— SKILL 语法陷阱

> 收录原 SKILL.md **第九节**。
> 写 `allegro.ilinit`、或拼 SKILL 表达式送进 SkillBridge 时最容易踩的语法坑。
>
> grep 关键词：`带点的名字`、`引号`、`let`、`when`、`自启动`、`平铺语句`

## 九、★★★ SKILL 语法陷阱（写 ilinit / SKILL 代码时必查）

### 9.1 带点的名字**不能**用 `'` 引成符号

```skill
'pyStartServer.ipc        ;; ❌ 读成 dotted pair (pyStartServer . ipc)，不是 symbol
(getd 'pyStartServer.ipc) ;; ❌ *Error* getd: argument #1 should be a symbol
                          ;;    (type template = "s") - (pyStartServer.ipc)
```

**涉及所有 `obj.attr` 形式的内部变量。** 要动态取符号用：
`concat`（`concat( Sx_arg1 ... ) => s_result`）或
`plist`（`plist( s_symbolName )`）—— 见 `sklangref/dataoperator.html`。

### 9.2 `let` 的变量绑定是「成对列表」，不是 `=`

```skill
(let ((x 1) (y 2)) ...)   ;; ✅ 文档写法（funcprog.html）
(let (x y) (x = 1) ...)   ;; ⚠️ 靠中缀赋值运算符工作，能跑但不标准
```

**推荐直接用文档写法**，或**干脆内联字符串**（最不容易出错）。

### 9.3 ★ 不要给自启动代码套 `when` 块 —— 用平铺语句

**规则**：初始化 / 自启动代码写成**多条平铺的顶层语句**，不要用
`when` / `unless` 包裹。理由：平铺时某一句报错不影响其它句；套 `when`
会制造"一处失败、全块中断"的单点故障。

★ 真实事故全过程（`getd` + `when` 让自启动静默失效）见 **2.5**，此处不赘述。

**推论（更通用）**：验证"改完初始化配置"必须验**实际效果**
（如 `netstat -ano | findstr <port>`），不能只看"有没有报错" ——
报错往往淹没在几十行日志中间。

### 9.4 其他已验证的语法点

| 写法 | 状态 |
|--|--|
| `(getShellEnvVar "NAME")` | ✅ `=> t_value \| nil`（未设置返 nil）|
| `(isFile "path")` | ✅ `=> t \| nil` |
| `(nequal a "0")` | ✅ nil ≠ "0" 成立 |
| `pyKillServer`（无括号） | ★ 只引用函数名**不调用** —— 但在 ilinit 里**是无害占位，生产版就这么写，保留即可** |
| `(axlFoo arg)` vs `axlFoo(arg)` | ★ 两种语法！`axlFoo(arg)` 是"调用"形式 |
| `?forceTcp` | ★ 别在自启动里加 —— 生产版没加，加了会改变启动模式 |

参考：`<Cadence>/doc/sklangref/`（SKILL 语言参考）
- `funcprog.html` — 函数/`getd`/`let`/`lambda`
- `inputoutput.html` — `isFile`/文件操作
- `environment.html` — `getShellEnvVar`/`setShellEnvVar`
- `dataoperator.html` — `concat`/`plist`/`getq`

---


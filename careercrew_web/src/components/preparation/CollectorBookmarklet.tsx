import { useMemo, useState } from "react"
import { Copy } from "lucide-react"

import { Button } from "@/components/ui/button"
import { useToast } from "@/hooks/useToast"

/**
 * 兜底采集通道：在任意招聘网站执行的书签脚本。
 *
 * 之所以只是兜底而不是主路径：Boss直聘 会拦截文本选中与开发者工具，而本脚本
 * 依赖页面内的 JS 执行，属于"站点愿意放行才可用"的通道。主路径是应用内收藏岗位
 * 时由后端 CDP 打开详情页抓取。
 *
 * 两个刻意的设计：
 * 1) 选中为空或过短时，不再指望用户选中，改为从 DOM 里按业务措辞（岗位职责 /
 *    任职要求…）找最小的达标容器——这正是 Boss"选不中"时的出路。
 * 2) 载荷放在 URL hash 而不是 query：JD 常有两三千字，实测 query 会撞上服务器
 *    请求行长度上限（容器约 80KB、Vite dev 仅 16KB），hash 不参与 HTTP 请求。
 */
export function CollectorBookmarklet() {
  const { showToast } = useToast()
  const code = useMemo(() => {
    const origin = window.location.origin
    const script = [
      "javascript:(function(){",
      "var d=document,jd='';",
      "try{jd=String(d.getSelection?d.getSelection():'');}catch(e){}",
      // 选区长度按去空白后计算，但保留原文（后续要原样入库）
      "if(jd.replace(/\\s/g,'').length<80){",
      "var k=/岗位职责|职位描述|工作职责|任职要求|任职资格|职位要求|岗位要求|工作内容/,best='',all=d.querySelectorAll('div,section,article,main');",
      "for(var i=0;i<all.length;i++){",
      "var t=all[i].innerText||'';",
      "if(t.length<80||t.length>30000||!k.test(t))continue;",
      "if(!best||t.length<best.length)best=t;",
      "}",
      "jd=best;",
      "}",
      "jd=jd.replace(/[\\u200b-\\u200f\\u202a-\\u202e\\u2060\\ufeff]/g,'').trim();",
      "var ttl=(d.title||'').replace(/\\s*[-_|]\\s*(BOSS直聘|Boss直聘|猎聘).*$/,'');",
      "var p='collect=1&url='+encodeURIComponent(location.href)"
        + "+'&title='+encodeURIComponent(ttl)"
        + "+'&jd='+encodeURIComponent(jd);",
      `var u=${JSON.stringify(origin)}+'/preparation#'+p;`,
      // 新标签页打开，保留用户原本的招聘站页面；被拦截时退回当前页跳转
      "var w=null;try{w=window.open(u,'_blank');}catch(e){}",
      "if(!w)location.href=u;",
      "})();",
    ].join("")
    return script
  }, [])
  const [copied, setCopied] = useState(false)

  return (
    <div className="mt-1.5">
      <code className="block max-h-[72px] overflow-y-auto break-all rounded-[6px] bg-surface-1 p-1.5 font-mono text-[10.5px] leading-relaxed text-ink-soft">
        {code}
      </code>
      <Button
        variant="outline"
        size="sm"
        className="mt-1 h-[22px] text-[11px]"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(code)
            setCopied(true)
            showToast("已复制，请新建书签并粘贴到网址栏")
          } catch {
            showToast("复制失败，请手动选择代码复制")
          }
        }}
      >
        <Copy className="h-3 w-3" /> {copied ? "已复制" : "复制书签代码"}
      </Button>
    </div>
  )
}

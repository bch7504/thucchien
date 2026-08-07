import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useAuth } from '../../context/AuthContext'
import { chatWithAgent, resumeAgent } from '../../api/agent'

const prompts = [
  { icon:'bi-sun', label:'Lên kế hoạch hôm nay', prompt:'Tổng hợp lịch, task và deadline của tôi hôm nay' },
  { icon:'bi-calendar-check', label:'Lịch sắp tới', prompt:'Tuần này tôi có những cuộc họp quan trọng nào?' },
  { icon:'bi-exclamation-diamond', label:'Deadline gần nhất', prompt:'Deadline nào đang đến gần và cần ưu tiên?' },
  { icon:'bi-journal-bookmark', label:'Tìm trong memory', prompt:'Tóm tắt những gì bạn nhớ về cách tôi làm việc' },
]

function describeInterrupt(interrupt) {
  const d = interrupt.draft
  if (interrupt.type === 'calendar_event') return `Bạn có muốn tạo sự kiện "${d.summary}" từ ${d.start} đến ${d.end}?`
  if (interrupt.type === 'reminder') return `Bạn có muốn đặt nhắc nhở "${d.title}" lúc ${d.due_at}?`
  return 'Bạn có muốn xác nhận hành động này?'
}

export default function PersonalAIChat({ onContext }) {
  const { token, user } = useAuth()
  const [draft,setDraft]=useState('')
  const [messages,setMessages]=useState([])
  const [threadId,setThreadId]=useState(null)
  const [pending,setPending]=useState(null)
  const [sending,setSending]=useState(false)

  const pushMessage = (msg) => setMessages(prev => [...prev, { id: Date.now() + Math.random(), ...msg }])

  const handleResult = (res) => {
    setThreadId(res.thread_id)
    if (res.status === 'interrupted') {
      setPending({ thread_id: res.thread_id, interrupt: res.interrupt })
      pushMessage({ text: describeInterrupt(res.interrupt), interrupt: res.interrupt })
    } else {
      pushMessage({ text: res.response || (res.status === 'error' ? 'Đã có lỗi xảy ra, thử lại sau.' : '') })
    }
  }

  const send = async (value=draft) => {
    if(!value.trim() || sending) return
    pushMessage({ own:true, text:value })
    setDraft('')
    setSending(true)
    try {
      const res = await chatWithAgent(token, { message: value, thread_id: threadId })
      handleResult(res)
    } catch (err) {
      pushMessage({ text: err.detail || 'Không gọi được AI Assistant, thử lại sau.' })
    } finally { setSending(false) }
  }

  const respond = async (approved) => {
    if (!pending || sending) return
    setSending(true)
    try {
      const res = await resumeAgent(token, { thread_id: pending.thread_id, approved })
      setPending(null)
      handleResult(res)
    } catch (err) {
      pushMessage({ text: err.detail || 'Không gọi được AI Assistant, thử lại sau.' })
    } finally { setSending(false) }
  }

  return <section className="personal-chat">
    <header className="personal-chat-header"><div className="personal-ai-avatar"><i className="bi bi-stars"/><span/></div><div><h3>Orbit Personal AI</h3><span><i/> Sẵn sàng hỗ trợ bạn</span></div><div className="personal-header-actions"><button className="context-mobile-btn" onClick={onContext}><i className="bi bi-layout-sidebar-reverse"/> Bối cảnh</button><button className="icon-btn" onClick={()=>{setMessages([]);setThreadId(null);setPending(null)}}><i className="bi bi-arrow-clockwise"/></button><button className="icon-btn"><i className="bi bi-three-dots"/></button></div></header>
    <div className="personal-messages">
      {messages.length===0 && <div className="personal-welcome"><motion.div initial={{scale:.85,opacity:0}} animate={{scale:1,opacity:1}} className="welcome-ai-mark"><i className="bi bi-stars"/></motion.div><span className="welcome-kicker">Chào {user?.display_name || 'bạn'}</span><h1>Hôm nay mình có thể<br/><em>giúp gì cho bạn?</em></h1><p>Hỏi mình về lịch, công việc, deadline hoặc thông tin từ các cuộc trò chuyện đã được cấp quyền.</p><div className="prompt-grid">{prompts.map(p=><motion.button whileHover={{y:-3}} whileTap={{scale:.98}} key={p.label} onClick={()=>send(p.prompt)}><span><i className={`bi ${p.icon}`}/></span><strong>{p.label}</strong><small>{p.prompt}</small><i className="bi bi-arrow-up-right"/></motion.button>)}</div></div>}
      <AnimatePresence>{messages.map(m=><motion.div key={m.id} initial={{opacity:0,y:8}} animate={{opacity:1,y:0}} className={`personal-message ${m.own?'own':''}`}>
        {!m.own&&<div className="message-ai-icon"><i className="bi bi-stars"/></div>}<div><div className="personal-message-bubble">{m.text}{m.interrupt && pending?.thread_id===threadId && <div className="d-flex gap-2 mt-2"><button className="btn btn-sm btn-primary" disabled={sending} onClick={()=>respond(true)}>Xác nhận</button><button className="btn btn-sm btn-light" disabled={sending} onClick={()=>respond(false)}>Huỷ</button></div>}</div><time>Bây giờ</time></div>
      </motion.div>)}</AnimatePresence>
      {sending && <div className="personal-message"><div className="message-ai-icon"><i className="bi bi-stars"/></div><div className="personal-message-bubble">Đang xử lý...</div></div>}
    </div>
    <div className="personal-composer-wrap"><div className="active-sources"><span><i className="bi bi-database-check"/> Đang dùng 4 nguồn</span><button>Chats <i className="bi bi-check"/></button><button>Tasks <i className="bi bi-check"/></button><button>Calendar <i className="bi bi-check"/></button><button>Memory <i className="bi bi-check"/></button></div><form className="personal-composer" onSubmit={e=>{e.preventDefault();send()}}><button type="button" className="icon-btn"><i className="bi bi-plus-lg"/></button><textarea rows="1" value={draft} onChange={e=>setDraft(e.target.value)} placeholder="Hỏi Orbit về công việc và lịch trình của bạn..." onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}}}/><button type="button" className="icon-btn"><i className="bi bi-mic"/></button><button className="personal-send" aria-label="Gửi" disabled={sending} onClick={e=>{e.preventDefault();send()}}><i className="bi bi-arrow-up"/></button></form><small>Orbit có thể mắc lỗi. Hãy kiểm tra lại thông tin quan trọng.</small></div>
  </section>
}

"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value == null ? "" : value).replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
let state = {projects:[], project:null, overview:null, config:{}, tab:"overview"};
let modalAction = null;
const labels = {overview:"Обзор", studio:"Студия", plan:"Контент-план", tasks:"Задачи и расходы", brand:"Бренд и источники", settings:"Настройки"};
const statusNames = {PAUSED:"На паузе", READY_FOR_REVIEW:"На просмотр", CHANGES_REQUIRED:"Нужны правки", APPROVED:"Подтверждён", SUPERSEDED:"Архив", QUEUED:"В очереди", RUNNING:"В работе", FAILED:"Ошибка", UNKNOWN:"Нужна сверка", SENT:"Опубликован", CANCELLED:"Отменён", SCHEDULED:"Запланирован", EXPIRED:"Окно пропущено", SUCCEEDED:"Готово"};
const fmt = n => "$" + Number(n || 0).toFixed(2);
const stamp = value => new Date(value).toLocaleString("ru-RU", {timeZone:state.project ? state.project.timezone : "Europe/Moscow", day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"});
async function api(path, options = {}) {
  const headers = {"X-Telok-Client":"dashboard", ...(options.headers || {})};
  if (options.body && !(options.body instanceof FormData)) {headers["Content-Type"]="application/json";options.body=JSON.stringify(options.body);}
  const response = await fetch("/api" + path, {...options, headers});
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) loginModal();
    throw Error(typeof data.detail === "string" ? data.detail : "Проверьте поля формы.");
  }
  return data;
}
function toast(text) {$("toast").textContent=text;$("toast").classList.add("visible");setTimeout(()=>$("toast").classList.remove("visible"),3500);}
function field(name, label, value="", type="text", hint="") {
  return '<div class="field"><label for="f-'+esc(name)+'">'+esc(label)+'</label>'+(type==="textarea"?'<textarea name="'+esc(name)+'" id="f-'+esc(name)+'">'+esc(value)+'</textarea>':'<input name="'+esc(name)+'" id="f-'+esc(name)+'" type="'+esc(type)+'" value="'+esc(value)+'">')+(hint?'<small class="hint">'+esc(hint)+'</small>':"")+"</div>";
}
function selectField(name,label,options) {return '<div class="field"><label>'+esc(label)+'</label><select name="'+esc(name)+'">'+options.map(o=>'<option value="'+esc(o[0])+'">'+esc(o[1])+'</option>').join("")+"</select></div>";}
function modal(title,body,action,submit="Сохранить") {$("modal-title").textContent=title;$("modal-body").innerHTML=body;$("modal-submit").textContent=submit;$("modal-error").textContent="";modalAction=action;$("modal").showModal();}
function closeModal() {$("modal").close();}
$("close-modal").onclick=closeModal;$("cancel-modal").onclick=closeModal;
$("modal-form").onsubmit=async event=>{event.preventDefault();$("modal-submit").disabled=true;try{const form=new FormData(event.target);await modalAction(form);closeModal();await refresh();}catch(error){$("modal-error").textContent=error.message;}finally{$("modal-submit").disabled=false;}};
function loginModal(){modal("Войти в редакцию",field("token","Ключ администратора","","password"),async f=>{await api("/login",{method:"POST",body:{token:f.get("token")}});},"Войти");}
async function refresh(){
  try {
    state.config=await api("/configuration");
    state.projects=await api("/projects");
    const selected=state.project ? state.project.id : localStorage.getItem("telok-project");
    state.project=state.projects.find(p=>p.id===selected)||state.projects[0]||null;
    $("project-select").innerHTML=state.projects.map(p=>'<option value="'+esc(p.id)+'"'+(state.project&&p.id===state.project.id?' selected':"")+'>'+esc(p.name)+"</option>").join("");
    state.overview=state.project ? await api("/projects/"+state.project.id+"/overview") : null;
    if(state.project)localStorage.setItem("telok-project",state.project.id);
    $("connection").textContent="Локальное пространство";
    render();
  } catch(error) {$("connection").textContent="Нет подключения";$("content").innerHTML='<div class="empty">'+esc(error.message)+"</div>";}
}
$("project-select").onchange=()=>{state.project=state.projects.find(p=>p.id===$("project-select").value);refresh();};
$("refresh").onclick=refresh;
$("add-project").onclick=()=>modal("Новый проект",field("name","Название")+field("description","Чем занимается бренд?","","textarea"),async f=>{const p=await api("/projects",{method:"POST",body:{name:f.get("name"),description:f.get("description")}});state.project=p;toast("Проект создан");},"Создать");
$("nav").onclick=event=>{const button=event.target.closest("[data-tab]");if(button){state.tab=button.dataset.tab;render();}};
$("create-work").onclick=()=>createWork();
$("pause-project").onclick=async()=>{if(!state.project)return;try{await api("/projects/"+state.project.id+"/control",{method:"POST",body:{action:state.project.paused?"resume":"pause"}});await refresh();}catch(e){toast(e.message);}};
function badge(v){return '<span class="badge '+(v.demo?"demo":v.status==="FAILED"||v.status==="UNKNOWN"?"failed":v.status==="SENT"?"sent":"")+'">'+esc(v.demo?"Дизайн-пример":statusNames[v.status]||v.status)+"</span>";}
function card(v){
  const visual=v.asset_ids.length?(v.format==="VIDEO_SHORT"?'<video class="card-video" controls preload="metadata" '+(v.brief.poster_asset_id?'poster="/api/assets/'+esc(v.brief.poster_asset_id)+'" ':"")+'src="/api/assets/'+esc(v.asset_ids[0])+'"></video>':'<img class="card-image" loading="lazy" src="/api/assets/'+esc(v.asset_ids[0])+'" alt="Материал '+esc(state.project.name)+'">'):'<div class="card-text-visual">'+esc(v.body.slice(0,100))+"</div>";
  return '<article class="card">'+visual+'<div class="card-body"><div class="card-meta">'+badge(v)+'<span class="muted" style="font-size:9px">v'+v.revision+'</span></div><h3>'+esc(v.body.split("\n")[0])+'</h3><p class="excerpt">'+esc(v.body)+'</p><div class="card-footer"><span>'+esc(stamp(v.created_at))+'</span><button data-open="'+esc(v.id)+'">Открыть ↗</button></div></div></article>';
}
function section(title,count,action){return '<div class="section-title"><h2>'+esc(title)+(count!=null?'<span class="count">'+count+"</span>":"")+"</h2>"+(action||"")+"</div>";}
function empty(text){return '<div class="empty"><span>✧</span>'+esc(text)+"</div>";}
function render(){
  document.querySelectorAll("[data-tab]").forEach(b=>b.classList.toggle("active",b.dataset.tab===state.tab));
  $("breadcrumb").textContent=labels[state.tab];
  $("page-title").textContent=state.tab==="overview"?"Редакция "+(state.project?state.project.name:"Telok"):labels[state.tab];
  const subtitles={overview:"От первой мысли до материала, которым хочется поделиться.",studio:"Здесь идеи получают форму. Выберите материал, чтобы продолжить.",plan:"Каждая тема — на своём месте, каждый день — с понятной целью.",tasks:"Прозрачный процесс: что сделано, что ожидает и сколько потрачено.",brand:"Контекст, на который может опереться вся команда.",settings:"Подключения, публикации и ритм работы."};
  $("page-subtitle").textContent=subtitles[state.tab];
  if(!state.project){$("notice").innerHTML="";$("content").innerHTML=empty("Создайте первый проект кнопкой + в боковой панели.");return;}
  $("pause-project").textContent=state.project.paused?"Снять проект с паузы":"Поставить проект на паузу";
  $("notice").innerHTML=!state.project.description?'<div class="notice"><span>У Telok пока есть название. Добавьте описание и проверенные факты, чтобы материалы отражали ваш бренд.</span><button data-action="brand">Настроить бренд →</button></div>':state.project.paused?'<div class="notice">Проект на паузе. Новые дорогие действия остановлены.</div>':"";
  const views={overview:overviewView,studio:studioView,plan:planView,tasks:tasksView,brand:brandView,settings:settingsView};
  $("content").innerHTML=views[state.tab]();
}
function overviewView(){
  const o=state.overview;
  const actual=o.versions.filter(v=>!v.demo&&v.status!=="SUPERSEDED");
  const ready=actual.filter(v=>v.status==="READY_FOR_REVIEW").length;
  const active=o.works.filter(w=>["QUEUED","RUNNING"].includes(w.status)).length;
  const sent=o.publications.filter(p=>p.status==="SENT").length;
  const charged=o.metrics.charged_usd;
  const stats=[["На просмотр",ready,"Готовы к вашему решению","◈"],["Задач в работе",active,"Процесс сохраняется в БД","≋"],["Опубликовано",sent,"С подтверждённым receipt","↗"],["Учтённый расход",fmt(charged),"Оценки и резервы — в задачах","◌"]];
  let html='<div class="stats">'+stats.map(s=>'<div class="stat"><span class="stat-symbol">'+s[3]+'</span><small>'+s[0]+'</small><b class="money">'+s[1]+'</b><div class="stat-foot">'+s[2]+"</div></div>").join("")+"</div>";
  html+='<div class="hero"><div class="hero-copy"><div class="eyebrow">МЫСЛЬ → ФОРМА → РЕЗУЛЬТАТ</div><h2>Дайте идее<br>немного пространства.</h2><p>Начните с одной задачи. Команда подготовит материал, а последнее слово останется за вами.</p><button data-action="create">Открыть студию ↗</button></div><div class="hero-art"><div class="art-card"><div class="mini-logo">telok</div><div class="rule"></div><strong>A little room<br>for a big idea.</strong><small>EDITORIAL STUDY / 001</small></div><div class="art-card second"><div class="mini-logo">telok</div><div class="rule"></div><strong>What comes<br>next?</strong><small>MAKE SPACE FOR IT</small></div></div></div>';
  const versions=o.versions.filter(v=>v.status!=="SUPERSEDED").slice(0,3);
  html+=section(actual.length?"Последние материалы":"Примеры оформления",versions.length,'<button data-action="studio">Все материалы →</button>');
  html+=versions.length?'<div class="cards">'+versions.map(card).join("")+"</div>":empty("Первый материал появится здесь.");
  html+='<div class="panel"><h3>Команда с понятными ролями</h3><div class="rows">'+[["Помощник","Понимает задачу и выбирает контекст"],["Стратег · генератор идей · директор","Исследуют, предлагают и выбирают направление"],["Автор · проверяющий","Создают материал и находят конкретные проблемы"]].map(r=>'<div class="row"><div class="row-title">'+r[0]+'<small>'+r[1]+'</small></div><span class="badge">'+(state.config.text_ready?"API подключен":"Ожидает API")+"</span></div>").join("")+"</div></div>";
  return html;
}
function studioView(){const versions=state.overview.versions.filter(v=>v.status!=="SUPERSEDED");return section("Материалы",versions.length,'<div class="row-actions"><button data-action="video">Собрать видео ＋</button><button data-action="manual">Добавить свой текст и файл ＋</button></div>')+(versions.length?'<div class="cards">'+versions.map(card).join("")+"</div>":empty("Создайте материал или добавьте свой."));}
function planView(){
  const plan=state.overview.plans[0];
  let html=section("Ближайшая неделя",null,'<button data-action="plan">Создать новый план ＋</button>');
  if(!plan)return html+empty("План появится после запроса команде.");
  const slots=plan.slots;
  const start=slots[0]?new Date(slots[0].date+"T12:00:00Z"):new Date();
  html+='<div class="calendar">';
  for(let i=0;i<7;i++){const day=new Date(start);day.setUTCDate(day.getUTCDate()+i);const iso=day.toISOString().slice(0,10);const values=slots.filter(s=>s.date===iso);
    html+='<div class="day"><small>'+esc(day.toLocaleDateString("ru-RU",{weekday:"short",timeZone:"UTC"}).toUpperCase())+'</small><span class="date">'+day.getUTCDate()+'</span>'+values.map(s=>'<div class="slot"><b>'+esc(s.title)+'</b><p>'+esc(s.goal)+'</p><span class="badge">'+esc(s.format==="IMAGE_POST"?"Текст + фото":"Текст")+'</span><p><button data-slot="'+esc(s.id)+'">'+(s.version_id?"Открыть материал →":s.work_id?"В работе":"Взять в работу →")+'</button></p></div>').join("")+"</div>";
  }return html+"</div>";
}
function tasksView(){
  const o=state.overview;
  let html='<div class="panel"><h3>Последние задачи</h3>'+(!o.works.length?empty("Пока нет запросов к команде."):o.works.map(w=>'<div class="row"><div class="row-title">'+esc({generate:"Производство",edit:"Правка",ideas:"Идеи",plan:"План",research:"Исследование",video:"Видео"}[w.kind]||w.kind)+'<small>'+esc(w.payload.brief||stamp(w.created_at))+'</small>'+(w.error?'<small style="color:var(--red)">'+esc(w.error)+"</small>":"")+'</div><div class="row-actions">'+badge(w)+(["FAILED","PAUSED"].includes(w.status)?'<button data-retry="'+esc(w.id)+'">Повторить</button>':["RUNNING","QUEUED"].includes(w.status)?'<button data-cancel="'+esc(w.id)+'">Отменить</button>':"")+"</div></div>").join(""))+"</div>";
  html+='<div class="panel"><h3>Отправки</h3>'+(o.publications.length?o.publications.map(p=>'<div class="row"><div class="row-title">'+esc(stamp(p.not_before))+'<small>'+esc(p.receipt.message_id?"Telegram message "+p.receipt.message_id:p.error||"Подтверждённая версия")+'</small></div><div class="row-actions">'+badge(p)+(["SCHEDULED","FAILED"].includes(p.status)?'<button data-schedule="'+esc(p.id)+'">Перенести</button><button data-unpublish="'+esc(p.id)+'">Отменить</button>':p.status==="UNKNOWN"?'<button data-reconcile="'+esc(p.id)+'">Сверить</button><button data-risk-retry="'+esc(p.id)+'">Новая попытка</button>':"")+"</div></div>").join(""):empty("Отправок пока нет."))+"</div>";
  html+='<div class="panel"><h3>Физические попытки и расходы</h3><p class="intro">ESTIMATED — консервативная оценка; UNKNOWN — резерв ещё удерживается. Это не подтверждённый счёт provider.</p>'+(o.attempts.length?o.attempts.map(a=>'<div class="row"><div class="row-title">'+esc(a.provider)+'<small>'+esc(stamp(a.created_at))+'</small></div><div><b class="money">'+fmt(a.charged||a.reserved)+'</b> <span class="badge">'+esc(a.status)+"</span></div></div>").join(""):empty("Платных API-вызовов ещё не было."))+"</div>";
  return html;
}
function brandView(){
  const p=state.project,b=p.brand;
  return '<div class="two-col"><div class="panel"><h3>Профиль бренда</h3><p class="intro">Название известно. Остальные сведения определяете вы.</p>'+field("description","Описание бренда",p.description,"textarea")+field("tone","Тон",b.tone||"")+field("facts","Утверждённые факты",(b.facts||[]).join("\n"),"textarea","Один факт на строку. Указывайте только сведения, которые можете подтвердить.")+field("rules","Обязательные правила",(b.rules||[]).join("\n"),"textarea")+'<button class="primary" data-action="save-brand">Сохранить версию '+(p.brand_revision+1)+'</button></div><div><div class="panel"><h3>Источники и утверждения</h3><p class="intro">Ссылка полезна, когда её фрагмент поддерживает конкретный факт.</p><button class="secondary" data-action="evidence">Добавить источник ＋</button>'+state.overview.evidence.map(e=>'<div class="row"><div><b class="row-title">'+esc(e.claim)+'</b><small>'+esc(e.excerpt.slice(0,170))+'</small></div><span class="badge">'+esc(e.assessment)+"</span></div>").join("")+"</div><div class=\"panel\"><h3>Визуальные референсы</h3><p class=\"intro\">Добавьте собственный файл для точного соответствия персонажу или стилю.</p><button class=\"secondary\" data-action=\"reference\">Загрузить reference ＋</button><div class=\"rows\">"+(b.references||[]).map(id=>'<div class="row"><img style="width:65px;height:65px;object-fit:cover;border-radius:8px" src="/api/assets/'+esc(id)+'"><small>'+esc(id.slice(0,12))+"</small></div>").join("")+"</div></div></div></div>";
}
function settingsView(){
  const c=state.config,p=state.project;
  return '<div class="two-col"><div><div class="panel"><h3>Подключения</h3>'+[["Текстовая модель",c.text_ready,c.text_model],["Генерация изображений",c.image_ready,c.image_model],["Telegram",c.telegram_ready,"Бот и разрешённый канал"],["Хранилище",true,c.storage==="s3"?"Приватное S3":"Локальное, для разработки"]].map(r=>'<div class="config-row"><div><b>'+esc(r[0])+'</b><small>'+esc(r[2])+'</small></div><span class="badge '+(r[1]?"":"demo")+'">'+(r[1]?"Подключено":"Нужна настройка")+"</span></div>").join("")+'<p class="intro">Ключи и денежные лимиты задаются в локальном .env. Секреты здесь не отображаются.</p></div><div class="panel"><h3>Канал публикации</h3>'+field("channel","@username или channel ID",p.channel_id)+"<p class=\"intro\">Настройте канал до создания финальной версии. Старый manifest не меняет адрес отправки.</p><button class=\"secondary\" data-action=\"save-channel\">Сохранить канал</button></div></div><div><div class=\"panel\"><h3>Ритм редакции</h3><p class=\"intro\">Система готовит материалы. Публикацию подтверждаете вы.</p>"+field("every","Интервал, часы",p.policy.every_hours||24,"number")+field("pending","Не больше материалов на просмотр",p.policy.max_pending||3,"number")+field("campaign-brief","Общая задача",p.policy.brief||"","textarea")+'<button class="primary" data-action="campaign">'+(p.policy.enabled?"Остановить кампанию":"Запустить SEMI_AUTO")+'</button></div><div class="panel"><h3>Денежные пределы</h3><div class="config-row"><b>На задачу</b><span>'+fmt(c.task_budget)+'</span></div><div class="config-row"><b>На день</b><span>'+fmt(c.daily_budget)+'</span></div><p class="intro">Нулевые лимиты блокируют платные вызовы. Неизвестный результат удерживает резерв.</p></div></div></div>';
}
function createWork(kind="generate",brief="",slotContext={}){
  if(!state.project)return;
  const hint=state.config.text_ready?"":"<div class=\"notice\">Для AI-производства нужны API-ключ и денежные лимиты. Свой материал можно добавить уже сейчас в Студии.</div>";
  modal(kind==="plan"?"Новый контент-план":kind==="ideas"?"Предложить идеи":"Новый материал",hint+selectField("kind","Задача",[["generate","Создать материал"],["ideas","Предложить идеи"],["plan","Составить план"],["research","Исследовать бренд"]])+field("brief","Что нужно подготовить?",brief,"textarea")+selectField("format","Формат",[["IMAGE_POST","Текст + изображение"],["TEXT_POST","Текст"]])+field("count","Количество идей / дней",kind==="plan"?7:3,"number")+field("start","Начало плана","2026-10-05","date"),async f=>{
    await api("/projects/"+state.project.id+"/work",{method:"POST",body:{kind:f.get("kind"),operation_id:crypto.randomUUID(),payload:{brief:f.get("brief"),format:f.get("format"),count:Number(f.get("count")),start:f.get("start"),...slotContext}}});toast("Задача передана команде");
  },"Передать команде");
  $("f-brief").focus();$("modal-body").querySelector('[name="kind"]').value=kind;
}
async function openVersion(id){
  const v=state.overview.versions.find(v=>v.id===id)||await api("/versions/"+id);
  const qa=v.qa.findings||[];
  const media=v.asset_ids.length?(v.format==="VIDEO_SHORT"?'<video class="modal-image" controls '+(v.brief.poster_asset_id?'poster="/api/assets/'+esc(v.brief.poster_asset_id)+'" ':"")+'src="/api/assets/'+esc(v.asset_ids[0])+'"></video>':'<img class="modal-image" src="/api/assets/'+esc(v.asset_ids[0])+'">'):"";
  const body=media+(v.format==="VIDEO_SHORT"?'<button type="button" class="secondary" data-video-edit="'+esc(v.id)+'">Изменить сцены и озвучку</button>':"")+'<div class="version-text">'+esc(v.body)+'</div><div class="qa">'+esc(v.qa.summary||"")+(v.demo?"<p>Дизайн-пример: создан локальным renderer, не является API pilot.</p>":"")+(v.qa.hard||[]).map(x=>'<p class="error">'+esc(x)+"</p>").join("")+qa.map(x=>'<p>'+esc(x.message)+"</p>").join("")+'</div>'+selectField("action","Действие",[["feedback","Оставить оценку"],...(v.format==="VIDEO_SHORT"?[]:[["edit","Заказать правку"]]),["approve","Подтвердить и отправить"]])+field("notes","Комментарий","","textarea")+selectField("decision","Оценка",[["revise","Нужны правки"],["accept","Принимаю результат"],["reject","Не подходит"]])+field("minutes","Время редактирования, минуты",0,"number")+selectField("scope","Область правки",[["text","Только текст"],["image","Изображение"],["all","Всё"]])+field("at","Публикация позже (необязательно)","","datetime-local","Время вашего браузера: "+Intl.DateTimeFormat().resolvedOptions().timeZone)+(qa.some(x=>x.category==="editorial")?'<label class="checkbox"><input name="accept_warnings" type="checkbox">Принимаю перечисленные редакционные замечания</label>':"");
  modal("Материал · версия "+v.revision,body,async f=>{
    const action=f.get("action");
    if(action==="approve"){await api("/versions/"+v.id+"/approve",{method:"POST",body:{accepted_findings:f.get("accept_warnings")?qa.filter(x=>x.category==="editorial").map(x=>x.message):[],at:f.get("at")?new Date(f.get("at")).toISOString():null}});toast("Подтверждённая версия поставлена на отправку");}
    else if(action==="edit"){await api("/projects/"+state.project.id+"/work",{method:"POST",body:{kind:"edit",payload:{version_id:v.id,brief:f.get("notes"),edit_scope:f.get("scope")}}});toast("Правка принята");}
    else {await api("/versions/"+v.id+"/feedback",{method:"POST",body:{decision:f.get("decision"),notes:f.get("notes"),editor_minutes:Number(f.get("minutes"))}});toast("Обратная связь сохранена");}
  },"Продолжить");
}
function manualModal(){modal("Добавить свой материал",field("body","Финальный текст","","textarea")+'<div class="field"><label>Изображение (необязательно)</label><input type="file" name="file" accept="image/png,image/jpeg"></div><label class="checkbox"><input type="checkbox" name="attest" required>Я проверил факты в своём тексте и подтверждаю сведения о собственном бренде</label>',async f=>{
  let asset=null;const file=f.get("file");if(file&&file.size){const upload=new FormData();upload.append("file",file);asset=(await api("/projects/"+state.project.id+"/assets",{method:"POST",body:upload})).id;}
  await api("/projects/"+state.project.id+"/manual",{method:"POST",body:{text:f.get("body"),asset_ids:asset?[asset]:[],owner_reviewed_facts:!!f.get("attest")}});toast("Создана новая версия");
},"Создать версию");}
document.body.addEventListener("click",async event=>{
  const t=event.target.closest("button");if(!t)return;
  try{
    if(t.dataset.videoEdit){const v=state.overview.versions.find(v=>v.id===t.dataset.videoEdit)||await api("/versions/"+t.dataset.videoEdit);closeModal();videoModal(v);return;}
    if(t.dataset.open){await openVersion(t.dataset.open);return;}
    if(t.dataset.slot){const plan=state.overview.plans.find(p=>p.slots.some(s=>s.id===t.dataset.slot));const slot=plan.slots.find(s=>s.id===t.dataset.slot);if(slot.version_id){await openVersion(slot.version_id);return;}createWork("generate",slot.title+"\nЦель: "+slot.goal+"\nДата: "+slot.date,{plan_id:plan.id,slot_id:slot.id});return;}
    if(t.dataset.retry||t.dataset.cancel){await api("/works/"+(t.dataset.retry||t.dataset.cancel)+"/control",{method:"POST",body:{action:t.dataset.retry?"retry":"cancel"}});await refresh();return;}
    if(t.dataset.unpublish){await api("/publications/"+t.dataset.unpublish+"/schedule",{method:"POST",body:{cancel:true}});await refresh();return;}
    if(t.dataset.schedule){modal("Перенести публикацию",field("at","Новая дата","","datetime-local"),async f=>{await api("/publications/"+t.dataset.schedule+"/schedule",{method:"POST",body:{at:new Date(f.get("at")).toISOString()}});});return;}
    if(t.dataset.riskRetry){modal("Новая попытка после UNKNOWN",'<p>Telegram мог принять предыдущую отправку. Проверьте канал и остановите предыдущего исполнителя.</p><label class="checkbox"><input name="stopped" type="checkbox" required>Предыдущий исполнитель остановлен</label><label class="checkbox"><input name="risk" type="checkbox" required>Я принимаю риск повторной публикации</label>',async f=>{await api("/publications/"+t.dataset.riskRetry+"/retry-unknown",{method:"POST",body:{previous_executor_stopped:!!f.get("stopped"),accept_duplicate_risk:!!f.get("risk")}});},"Создать новую попытку");return;}
    if(t.dataset.reconcile){modal("Сверить UNKNOWN",field("message_id","Telegram message ID","","number")+field("chat_id","Channel ID",state.project.channel_id),async f=>{await api("/publications/"+t.dataset.reconcile+"/reconcile",{method:"POST",body:{message_id:Number(f.get("message_id")),chat_id:f.get("chat_id")}});});return;}
    switch(t.dataset.action){
      case"create":createWork();break;
      case"studio":case"brand":state.tab=t.dataset.action;render();break;
      case"plan":createWork("plan");break;
      case"manual":manualModal();break;
      case"video":videoModal();break;
      case"save-brand":{const b={...state.project.brand,tone:$("f-tone").value,facts:$("f-facts").value.split("\n").filter(Boolean),rules:$("f-rules").value.split("\n").filter(Boolean)};
        await api("/projects/"+state.project.id+"/brand",{method:"PUT",body:{expected_revision:state.project.brand_revision,description:$("f-description").value,brand:b}});toast("Версия бренда сохранена");await refresh();break;}
      case"save-channel":await api("/projects/"+state.project.id+"/channel",{method:"POST",body:{channel_id:$("f-channel").value}});toast("Канал сохранён");await refresh();break;
      case"campaign":await api("/projects/"+state.project.id+"/campaign",{method:"POST",body:{enabled:!state.project.policy.enabled,every_hours:Number($("f-every").value),max_pending:Number($("f-pending").value),brief:$("f-campaign-brief").value}});await refresh();break;
      case"evidence":modal("Подтвердить утверждение",field("claim","Утверждение")+field("excerpt","Поддерживающий фрагмент","","textarea")+field("url","Ссылка (если есть)")+selectField("kind","Источник",[["owner_statement","Сведения владельца о своём продукте"],["external","Внешний источник"]])+selectField("assessment","Что подтверждает фрагмент?",[["insufficient","Недостаточно данных"],["supports","Поддерживает"],["contradicts","Противоречит"]]),async f=>{await api("/projects/"+state.project.id+"/evidence",{method:"POST",body:Object.fromEntries(f)});});break;
      case"reference":modal("Визуальный reference",'<div class="field"><label>Файл PNG / JPEG</label><input type="file" name="file" accept="image/png,image/jpeg" required></div>',async f=>{const upload=new FormData();upload.append("file",f.get("file"));const asset=await api("/projects/"+state.project.id+"/assets",{method:"POST",body:upload});await api("/projects/"+state.project.id+"/brand",{method:"PUT",body:{expected_revision:state.project.brand_revision,brand:{...state.project.brand,references:[...(state.project.brand.references||[]),asset.id]}}});});break;
    }
  }catch(error){toast(error.message);}
});
refresh();
setInterval(()=>{if(!$("modal").open&&["tasks","overview","studio"].includes(state.tab))refresh();},10000);


function videoModal(version=null){
  const images=state.overview.assets.filter(a=>["image/png","image/jpeg"].includes(a.mime));
  if(!images.length){toast("Сначала добавьте изображение или создайте материал.");return;}
  const previous=version?version.brief.storyboard:null;
  const count=previous?previous.scenes.length:3;
  const options=images.map(a=>[a.id,(a.info.demo?"Дизайн-пример · ":"Изображение · ")+a.id.slice(0,8)]);
  let body=field("title","Заголовок видео",previous?previous.title:"");
  for(let i=0;i<count;i++){const scene=previous?previous.scenes[i]:{duration:8,caption:"",narration:""};
    body+='<div class="panel"><h3>Сцена '+(i+1)+'</h3>'+selectField("image"+i,"Изображение",options)+field("duration"+i,"Длительность, секунды",scene.duration,"number")+field("caption"+i,"Текст на экране",scene.caption)+field("narration"+i,"Озвучка",scene.narration,"textarea")+'</div>';
  }
  body+='<div class="field"><label>Готовая озвучка WAV / MP3 (необязательно)</label><input type="file" name="audio" accept="audio/wav,audio/mpeg"></div>'+selectField("locale","Язык локальной озвучки",[["ru-RU","Русский"],["en-US","English"]])+'<p class="hint">Плавное движение изображений. Без аудиофайла используется установленный голос Windows; на сервере загрузите озвучку. При изменении речи готовый audio нужно заменить.</p><label class="checkbox"><input name="attest" type="checkbox" required>Я проверил текст и озвучку</label>';
  modal(version?"Правка ролика · новая версия":"Видео из трёх сцен",body,async f=>{
    let audio=version?version.brief.audio_asset_id:null;const file=f.get("audio");if(file&&file.size){const form=new FormData();form.append("file",file);audio=(await api("/projects/"+state.project.id+"/assets",{method:"POST",body:form})).id;}
    const scenes=Array.from({length:count},(_,i)=>({image_asset_id:f.get("image"+i),duration:Number(f.get("duration"+i)),caption:f.get("caption"+i),narration:f.get("narration"+i)}));
    await api("/projects/"+state.project.id+"/video",{method:"POST",body:{operation_id:crypto.randomUUID(),previous_video_id:version?version.id:null,owner_reviewed_facts:!!f.get("attest"),tts:audio?null:"windows",locale:f.get("locale"),audio_asset_id:audio,storyboard:{title:f.get("title"),scenes}}});
    toast("Видеозадача передана на сборку");
  },"Собрать видео");
  for(let i=0;i<count;i++)$("modal-body").querySelector('[name="image'+i+'"]').value=previous?previous.scenes[i].image_asset_id:images[i%images.length].id;
  if(version)$("modal-body").querySelector('[name="locale"]').value=version.brief.locale||"ru-RU";
}

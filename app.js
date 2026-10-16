(()=>{
"use strict";

const C=Object.assign({
  brandName:"Discord Bot",
  shortName:"B",
  creator:"Your Name",
  version:"v1.0.0",
  inviteUrl:"",
  supportUrl:"",
  statusEndpoint:""
},window.BOT_SITE_CONFIG||{});

const fallbackCommands=[
  {name:"/spam",category:"Core",title:"多頻道訊息發送",desc:"選擇多個文字頻道並發送指定內容與次數。",params:"content / count",example:"/spam content:你好 count:10"},
  {name:"/stopspam",category:"Control",title:"停止進行中的發送",desc:"停止自己目前的工作；具備相應權限者可協助停止其他工作。",params:"target?",example:"/stopspam"},
  {name:"/history",category:"History & Cleanup",title:"歷史訊息清理",desc:"搜尋指定頻道範圍的歷史訊息，並依成員或內容篩選。",params:"channels / member / content",example:"/history"}
];

const commands=Array.isArray(window.BOT_COMMANDS)&&window.BOT_COMMANDS.length?window.BOT_COMMANDS:fallbackCommands;
const features=Array.isArray(window.BOT_FEATURES)?window.BOT_FEATURES:[];
const $=(s,r=document)=>r.querySelector(s);
const $$=(s,r=document)=>Array.from(r.querySelectorAll(s));

function safeText(value){return String(value??"");}
function toast(message,warning=false){
  const item=document.createElement("div");
  item.className="toast"+(warning?" warn":"");
  item.textContent=message;
  document.body.appendChild(item);
  requestAnimationFrame(()=>item.classList.add("show"));
  setTimeout(()=>{
    item.classList.remove("show");
    setTimeout(()=>item.remove(),220);
  },2600);
}

function setupConfig(){
  $$("[data-brand-name]").forEach(el=>el.textContent=C.brandName);
  const title=document.querySelector("title");
  if(title)title.textContent=C.brandName+" — Official";
  const version=$("#versionText");
  if(version)version.textContent=C.version;
  const year=$("#year");
  if(year)year.textContent=new Date().getFullYear();

  $$(".invite-link").forEach(link=>{
    if(C.inviteUrl){
      link.href=C.inviteUrl;
      link.target="_blank";
      link.rel="noopener noreferrer";
    }else{
      link.hidden=true;
      link.removeAttribute("href");
      link.setAttribute("aria-hidden","true");
    }
  });

  $$(".support-link").forEach(link=>{
    if(C.supportUrl){
      link.href=C.supportUrl;
      link.target="_blank";
      link.rel="noopener noreferrer";
    }else{
      link.hidden=true;
      link.removeAttribute("href");
      link.setAttribute("aria-hidden","true");
    }
  });
}

function setupTheme(){
  const button=$("#themeBtn");
  if(!button)return;
  let theme=localStorage.getItem("bot-site-theme");
  if(theme!=="light"&&theme!=="dark"){
    theme=matchMedia("(prefers-color-scheme:light)").matches?"light":"dark";
  }
  const apply=()=>{
    document.documentElement.dataset.theme=theme;
    button.textContent=theme==="light"?"☼":"◐";
    button.setAttribute("aria-label",theme==="light"?"切換深色模式":"切換淺色模式");
    localStorage.setItem("bot-site-theme",theme);
  };
  apply();
  button.addEventListener("click",()=>{
    theme=theme==="light"?"dark":"light";
    apply();
  });
}

function setupMenu(){
  const button=$("#menuBtn");
  const nav=$("#navLinks");
  if(!button||!nav)return;
  button.addEventListener("click",()=>{
    const open=nav.classList.toggle("mobile-open");
    button.setAttribute("aria-expanded",String(open));
  });
  $$("a",nav).forEach(link=>link.addEventListener("click",()=>{
    nav.classList.remove("mobile-open");
    button.setAttribute("aria-expanded","false");
  }));
}

function renderFeatures(){
  const root=$("#featureGrid");
  if(!root)return;
  if(!features.length){
    root.innerHTML="";
    return;
  }
  root.innerHTML=features.map((feature,index)=>
    '<article class="surface interactive reveal in">' +
      '<div class="icon-box">'+safeText(String(index+1).padStart(2,"0"))+'</div>' +
      '<div class="kicker">'+safeText(feature.name)+'</div>' +
      '<h3>'+safeText(feature.title)+'</h3>' +
      '<p>'+safeText(feature.desc)+'</p>' +
    '</article>'
  ).join("");
  activateInteractive(root);
}

function commandData(filter="all",query=""){
  const q=query.trim().toLowerCase();
  return commands.filter(command=>{
    const matchesFilter=filter==="all"||command.category===filter;
    const hay=[command.name,command.category,command.title,command.desc,command.params].map(safeText).join(" ").toLowerCase();
    return matchesFilter&&(!q||hay.includes(q));
  });
}

function renderCommands(filter="all",query=""){
  const root=$("#commandList");
  if(!root)return;
  const data=commandData(filter,query);
  if(!data.length){
    root.innerHTML='<div class="surface">找不到符合條件的指令。</div>';
    return;
  }
  root.innerHTML=data.map(command=>
    '<article class="surface interactive command-card reveal">'+
      '<div>'+
        '<div class="command-name">'+
          '<code>'+safeText(command.name)+'</code>'+
          '<span class="pill">'+safeText(command.category)+'</span>'+
          '<span class="pill">'+safeText(command.params)+'</span>'+
        '</div>'+
        '<h3>'+safeText(command.title)+'</h3>'+
        '<p>'+safeText(command.desc)+'</p>'+
      '</div>'+
      '<div class="command-side">'+
        '<span class="example" title="'+safeText(command.example)+'">'+safeText(command.example)+'</span>'+
        '<button class="copy-btn" type="button" data-copy="'+encodeURIComponent(command.example||"")+'">複製</button>'+
      '</div>'+
    '</article>'
  ).join("");
  activateInteractive(root);
  $$(".copy-btn",root).forEach(button=>{
    button.addEventListener("click",async()=>{
      const value=decodeURIComponent(button.dataset.copy||"");
      try{
        await navigator.clipboard.writeText(value);
        button.textContent="已複製";
        toast("指令已複製。");
        setTimeout(()=>button.textContent="複製",1200);
      }catch{
        toast(value);
      }
    });
  });
}

function setupCommands(){
  renderCommands();
  $$(".filter").forEach(button=>{
    button.addEventListener("click",()=>{
      $$(".filter").forEach(item=>item.classList.remove("active"));
      button.classList.add("active");
      renderCommands(button.dataset.filter||"all",$("#commandSearch")?.value||"");
    });
  });
  const search=$("#commandSearch");
  if(search)search.addEventListener("input",()=>renderCommands($(".filter.active")?.dataset.filter||"all",search.value));
  const clear=$("#clearSearch");
  if(clear)clear.addEventListener("click",()=>{
    if(search)search.value="";
    renderCommands($(".filter.active")?.dataset.filter||"all","");
  });
}

function setupButtonGlow(){
  $$(".btn,.icon-btn,.copy-btn,.filter").forEach(button=>{
    button.addEventListener("pointermove",event=>{
      const rect=button.getBoundingClientRect();
      button.style.setProperty("--btn-x",((event.clientX-rect.left)/rect.width*100)+"%");
      button.style.setProperty("--btn-y",((event.clientY-rect.top)/rect.height*100)+"%");
    });
  });
}

function activateInteractive(root=document){
  if(!matchMedia("(pointer:fine)").matches)return;
  $$(".interactive",root).forEach(card=>{
    if(card.dataset.interactiveBound==="1")return;
    card.dataset.interactiveBound="1";
    card.addEventListener("pointermove",event=>{
      const rect=card.getBoundingClientRect();
      const x=(event.clientX-rect.left)/rect.width;
      const y=(event.clientY-rect.top)/rect.height;
      const rx=(0.5-y)*7;
      const ry=(x-0.5)*8;
      card.style.setProperty("--glow-x",(x*100)+"%");
      card.style.setProperty("--glow-y",(y*100)+"%");
      card.style.setProperty("--rx",rx.toFixed(2)+"deg");
      card.style.setProperty("--ry",ry.toFixed(2)+"deg");
      card.style.transform="perspective(900px) translateY(-7px) scale(1.022) rotateX("+rx.toFixed(2)+"deg) rotateY("+ry.toFixed(2)+"deg)";
    });
    card.addEventListener("pointerleave",()=>{
      card.style.transform="";
      card.style.removeProperty("--glow-x");
      card.style.removeProperty("--glow-y");
      card.style.removeProperty("--rx");
      card.style.removeProperty("--ry");
    });
  });
}

function setupVisualGrid(){
  const grid=$("#visualGrid");
  if(!grid)return;
  grid.innerHTML=Array.from({length:36},(_,index)=>"<i data-cell='"+index+"'></i>").join("");
  const cells=$$("i",grid);
  let previous=-1;
  cells.forEach((cell,index)=>{
    cell.addEventListener("pointerenter",()=>{
      if(previous>=0)cells[previous].classList.remove("hot");
      cell.classList.add("hot");
      previous=index;
    });
  });
  grid.addEventListener("pointerleave",()=>{
    cells.forEach(cell=>cell.classList.remove("hot"));
    previous=-1;
  });
}

function setupReveal(){
  const targets=$$(".reveal");
  if(!("IntersectionObserver" in window)){
    targets.forEach(el=>el.classList.add("in"));
    return;
  }
  const observer=new IntersectionObserver(entries=>{
    entries.forEach(entry=>{
      if(entry.isIntersecting){
        entry.target.classList.add("in");
        observer.unobserve(entry.target);
      }
    });
  },{threshold:.12});
  targets.forEach(el=>observer.observe(el));
}

function setupMouse(){
  if(!matchMedia("(pointer:fine)").matches)return;
  let raf=0;
  document.addEventListener("pointermove",event=>{
    if(raf)return;
    raf=requestAnimationFrame(()=>{
      document.documentElement.style.setProperty("--mouse-x",event.clientX+"px");
      document.documentElement.style.setProperty("--mouse-y",event.clientY+"px");
      raf=0;
    });
  },{passive:true});
}

function setupScrollSpy(){
  const sections=$$("main section[id]");
  const links=$$("#navLinks a");
  if(!("IntersectionObserver" in window)||!sections.length)return;
  const map=new Map(links.map(link=>[link.getAttribute("href")?.slice(1),link]));
  const observer=new IntersectionObserver(entries=>{
    entries.forEach(entry=>{
      if(entry.isIntersecting){
        links.forEach(link=>link.classList.remove("active"));
        map.get(entry.target.id)?.classList.add("active");
      }
    });
  },{rootMargin:"-35% 0px -55% 0px",threshold:0});
  sections.forEach(section=>observer.observe(section));
}

function setupParallax(){
  if(!matchMedia("(pointer:fine)").matches)return;
  const visual=$(".hero-visual");
  if(!visual)return;
  visual.addEventListener("pointermove",event=>{
    const rect=visual.getBoundingClientRect();
    const x=(event.clientX-rect.left)/rect.width-.5;
    const y=(event.clientY-rect.top)/rect.height-.5;
    const consoleEl=$(".console",visual);
    const floatA=$(".float-a",visual);
    const floatB=$(".float-b",visual);
    if(consoleEl)consoleEl.style.transform="translate3d("+(x*8).toFixed(1)+"px,"+(y*8).toFixed(1)+"px,0)";
    if(floatA)floatA.style.transform="translate3d("+(x*-12).toFixed(1)+"px,"+(y*-8).toFixed(1)+"px,0)";
    if(floatB)floatB.style.transform="translate3d("+(x*10).toFixed(1)+"px,"+(y*7).toFixed(1)+"px,0)";
  });
  visual.addEventListener("pointerleave",()=>{
    [$(".console",visual),$(".float-a",visual),$(".float-b",visual)].forEach(el=>{if(el)el.style.transform=""});
  });
}

async function loadStatus(){
  if(!C.statusEndpoint){
    $("#statusTitle").textContent="尚未設定即時狀態";
    $("#statusDetail").textContent="網站尚未連接狀態資料來源，因此目前無法提供即時服務狀態。";
    $("#lastUpdate").textContent="—";
    $("#heroStatus").textContent="未設定";
    $("#heroState").textContent="STATUS NOT CONFIGURED";
    $("#statusDot").className="status-dot-large";
    return;
  }
  try{
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),6500);
    const response=await fetch(C.statusEndpoint,{cache:"no-store",signal:controller.signal,headers:{Accept:"application/json"}});
    clearTimeout(timer);
    if(!response.ok)throw new Error();
    const data=await response.json();
    const state=data.status==="online"||data.status==="operational"?"operational":data.status==="degraded"?"degraded":data.status==="offline"?"offline":"unknown";
    const labels={operational:"服務正常運作",degraded:"部分功能異常",offline:"服務暫時離線",unknown:"狀態資料無法確認"};
    $("#statusTitle").textContent=labels[state];
    $("#statusDetail").textContent=data.message||"服務狀態已更新。";
    $("#lastUpdate").textContent=data.updatedAt?new Date(data.updatedAt).toLocaleString("zh-TW"):"剛剛更新";
    $("#heroStatus").textContent=state==="operational"?"正常":state==="degraded"?"部分異常":state==="offline"?"離線":"未知";
    $("#heroState").textContent=state==="operational"?"SERVICE ONLINE":"SERVICE CHECK";
    $("#statusDot").className="status-dot-large "+state;
  }catch{
    $("#statusTitle").textContent="目前無法確認";
    $("#statusDetail").textContent="暫時取得不到即時狀態資料。";
    $("#lastUpdate").textContent="—";
    $("#heroStatus").textContent="未知";
    $("#heroState").textContent="SERVICE CHECK";
    $("#statusDot").className="status-dot-large";
  }
}


function setupGallery(){
  const grid=$("#galleryGrid");
  if(!grid)return;
  const iconSets={
    commands:["↗","Ⅱ","⌕","/","✦","⌁","↻","⌘"],
    usage:["01","02","03","→","✓","◌","＋","→"],
    visual:["✦","◒","◈","◌","◎","◇","✧","◍"],
    interaction:["⌁","↗","✦","⌕","＋","◒","◇","↻"],
    tips:["i","!","✓","?","•","→","⌁","✦"]
  };
  const bases=[
    ["選擇頻道","先選需要操作的文字頻道。"],
    ["輸入內容","把要發送的內容填入指令參數。"],
    ["設定次數","依需求設定這次工作的發送數量。"],
    ["查看工作","送出後注意 Discord 回覆的結果。"],
    ["停止工作","需要時可以使用停止指令結束自己的工作。"],
    ["指定對象","有相應權限時可以處理指定使用者的工作。"],
    ["歷史搜尋","先縮小頻道範圍，再找需要的訊息。"],
    ["成員篩選","可以從指定成員方向縮小搜尋範圍。"],
    ["內容篩選","用關鍵字快速定位相關訊息。"],
    ["權限確認","操作前先確認 Bot 有需要的 Discord 權限。"],
    ["回覆結果","成功或失敗都應該看清楚 Bot 的回覆。"],
    ["簡單流程","選擇 → 執行 → 查看結果。"],
    ["少一步操作","把重複性的頻道操作集中處理。"],
    ["集中管理","讓常用管理動作有固定入口。"],
    ["快速定位","搜尋欄可以直接找到對應指令。"],
    ["一鍵複製","指令範例可以直接複製。"],
    ["分類瀏覽","依類型切換內容會更快。"],
    ["手機也能看","版面會隨螢幕寬度重新排列。"],
    ["深色模式","降低夜間瀏覽時的視覺干擾。"],
    ["淺色模式","換成更明亮的背景與介面。"]
  ];
  const categories=[
    ["commands","指令",1],
    ["usage","使用",1],
    ["visual","視覺",1],
    ["interaction","互動",1],
    ["tips","提示",1],
    ["commands","指令",2]
  ];
  const items=[];
  let n=1;
  categories.forEach(([category,label,multiplier])=>{
    bases.forEach((base,index)=>{
      const icon=iconSets[category][index%iconSets[category].length];
      items.push({id:n++,category,label,icon,title:base[0],desc:base[1]});
    });
  });
  function render(){
    const active=$(".filter[data-gallery-filter].active")?.dataset.galleryFilter||"all";
    const query=$("#gallerySearch")?.value.trim().toLowerCase()||"";
    const data=items.filter(item=>(active==="all"||item.category===active)&&(!query||(item.title+" "+item.desc+" "+item.label).toLowerCase().includes(query)));
    if($("#galleryTotal"))$("#galleryTotal").textContent="· "+data.length;
    grid.innerHTML=data.length?data.map(item=>(
      '<article class="gallery-card interactive reveal in" data-gallery-item>'+
        '<span class="gallery-tag">'+item.label+'</span>'+
        '<span class="gallery-index">DETAIL '+String(item.id).padStart(3,"0")+'</span>'+
        '<div class="gallery-icon">'+item.icon+'</div>'+
        '<strong>'+item.title+'</strong>'+
        '<p>'+item.desc+'</p>'+
      '</article>'
    )).join(""):'<div class="gallery-empty">沒有找到符合條件的內容。</div>';
    activateInteractive(grid);
  }
  $$("#galleryFilters .filter").forEach(button=>button.addEventListener("click",()=>{
    $$("#galleryFilters .filter").forEach(x=>x.classList.remove("active"));
    button.classList.add("active");
    render();
  }));
  $("#gallerySearch")?.addEventListener("input",render);
  render();
}

function init(){
  setupConfig();
  setupTheme();
  setupMenu();
  renderFeatures();
  setupCommands();
  setupVisualGrid();
  setupButtonGlow();
  activateInteractive(document);
  setupReveal();
  setupMouse();
  setupScrollSpy();
  setupParallax();
  setupGallery();
  loadStatus();
  if(C.statusEndpoint)setInterval(loadStatus,30000);
}

document.addEventListener("DOMContentLoaded",init);
})();
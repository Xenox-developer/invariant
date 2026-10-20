// Static trusted inspector. It never imports or executes environment software.
use std::{fs, io::Write, os::raw::{c_char, c_int, c_ulong}, process};
#[repr(C)] struct Statvfs { bsize:c_ulong, frsize:c_ulong, blocks:c_ulong, bfree:c_ulong, bavail:c_ulong,
    files:c_ulong, ffree:c_ulong, favail:c_ulong, fsid:c_ulong, flag:c_ulong, namemax:c_ulong, spare:[c_int;6] }
#[repr(C)] struct Rlimit { current:u64, max:u64 }
extern "C" {
    fn statvfs(path:*const c_char, buf:*mut Statvfs)->c_int;
    fn getrlimit(resource:c_int, limit:*mut Rlimit)->c_int;
    fn getuid()->u32;
    fn socket(domain:c_int, kind:c_int, protocol:c_int)->c_int;
    fn close(fd:c_int)->c_int;
    fn prctl(option:c_int, ...)->c_int;
}
fn stats(path:&str)->Statvfs {
    let mut out:Statvfs=unsafe{std::mem::zeroed()};
    let p=std::ffi::CString::new(path).unwrap();
    assert_eq!(unsafe{statvfs(p.as_ptr(), &mut out)},0,"statvfs"); out
}
fn read(path:&str)->String {fs::read_to_string(path).expect("kernel file")}
fn limit(name:&str)->String {let value=read(&format!("/sys/fs/cgroup/{name}")); if value.trim()=="max" {"null".into()} else {value.trim().parse::<u64>().unwrap().to_string()}}
fn socket_status(family:c_int)->&'static str {
    let fd=unsafe{socket(family,1,0)};
    if fd>=0 {unsafe{close(fd)}; "allowed"} else {
        match std::io::Error::last_os_error().raw_os_error() {Some(1)|Some(13)=>"denied",_=>"unverified"}
    }
}
fn reclaim(path:&std::path::Path, uid:u32, gid:u32, remaining:&mut usize, depth:usize) {
    assert!(*remaining>0 && depth<=256,"ownership traversal limit"); *remaining-=1;
    let info=fs::symlink_metadata(path).expect("ownership metadata");
    if info.is_dir() {
        for child in fs::read_dir(path).expect("ownership directory") {
            reclaim(&child.expect("ownership entry").path(),uid,gid,remaining,depth+1);
        }
    }
    // Never follow a workload symlink, including one pointing outside its mount.
    std::os::unix::fs::lchown(path,Some(uid),Some(gid)).expect("ownership transfer");
}
fn main() {
    // Other unprivileged container processes cannot ptrace this inspection.
    assert_eq!(unsafe{prctl(4,0,0,0,0)},0);
    let args:Vec<String>=std::env::args().collect();
    if args.get(1).map(String::as_str)==Some("reclaim") {
        assert!(args.len()==4 || (args.len()==5 && args[4]=="output"));
        let uid=args[2].parse::<u32>().unwrap(); let gid=args[3].parse::<u32>().unwrap();
        assert!(uid!=0,"non-root operator required");
        let mut remaining=1_000_000;
        reclaim(std::path::Path::new("/workspace"),uid,gid,&mut remaining,0);
        if args.len()==5 {reclaim(std::path::Path::new("/__invariant_output"),uid,gid,&mut remaining,0);}
        println!("{{\"reclaimed\":true}}");return
    }
    if args.get(1).map(String::as_str)==Some("pressure") {
        let events=read("/sys/fs/cgroup/memory.events");
        let n=events.lines().find_map(|s|s.strip_prefix("oom_kill ")).unwrap().parse::<u64>().unwrap();
        println!("{{\"oom_kill\":{n}}}");return
    }
    if let Some(mode)=args.get(1).and_then(|s|s.strip_prefix("startup-")) {
        if mode=="ro" {assert!(stats("/workspace").flag&1!=0);} else {
            let p=format!("/workspace/.invariant-startup-{}",process::id());
            let mut f=fs::OpenOptions::new().write(true).create_new(true).open(&p).unwrap();
            f.write_all(b"probe").unwrap(); drop(f); fs::remove_file(p).unwrap();
        }
        println!("{{\"ready\":true}}");return
    }
    let status=read("/proc/self/status");
    let field=|name:&str|status.lines().find_map(|s|s.strip_prefix(name)).unwrap().trim().to_owned();
    let cap=|name:&str|u64::from_str_radix(&field(name),16).unwrap();
    let cpu=read("/sys/fs/cgroup/cpu.max"); let words:Vec<&str>=cpu.split_whitespace().collect();
    let cpu=if words[0]=="max" {"null".into()} else {(words[0].parse::<f64>().unwrap()/words[1].parse::<f64>().unwrap()).to_string()};
    let mut nofile=Rlimit{current:0,max:0}; assert_eq!(unsafe{getrlimit(7,&mut nofile)},0);
    let scratch=stats("/scratch");
    println!(concat!("{{\"cpus\":{},\"memory_bytes\":{},\"extra_swap_bytes\":{},\"processes\":{},",
      "\"file_descriptors\":[{},{}],\"scratch_bytes\":{},\"uid\":{},",
      "\"capabilities_effective\":{},\"capabilities_bounding\":{},\"no_new_privileges\":{},\"seccomp_filter\":{},",
      "\"root_read_only\":{},\"workspace_read_only\":{},\"policy_read_only\":{},\"control_read_only\":{},",
      "\"network_sockets\":{{\"ipv4\":\"{}\",\"ipv6\":\"{}\"}}}}"),
      cpu,limit("memory.max"),limit("memory.swap.max"),limit("pids.max"),nofile.current,nofile.max,
      scratch.blocks*scratch.frsize,unsafe{getuid()},cap("CapEff:"),cap("CapBnd:"),field("NoNewPrivs:")=="1",field("Seccomp:")=="2",
      stats("/").flag&1!=0,stats("/workspace").flag&1!=0,stats("/workspace/.invariant").flag&1!=0,stats("/__invariant").flag&1!=0,
      socket_status(2),socket_status(10));
}

# My Learning Log

making a Dropbox clone to understand how backend systems and file synchronization work.

throughs and checklist and learnings

## Progress Checklist

### Backend
- [x] Set up FastAPI
- [x] PostgreSQL
- [x] database models
- [ ] understand how Alembic migrations work (not so much watch one more video)
- [x] file upload and download
- [x] file versioning
- [x] content hashing
- [x] soft deletion
- [x] change journal
After Meeting
- [x] Refactor into controller, service, and repository layers
- [] do learnign and readme files
- [] pull request github
- [] do endpoints for forlder handling (just like the ones done with files)

### Sync (client)
- [x] Create an endpoint to retrieve changes
- [ ] Build a local sync client
- [ ] Detect local file changes
- [ ] Sync between two clients
- [ ] Handle offline changes
- [ ] Handle conflicts

## understadnings

### File Versioning
*instead of replacing the old file when someone uploads an update I can keep the previous version and save the new one separately and have numbers continue instead of going back

*soft deletion to help in the process of rich versioning towards end of project

### no locking mechanism
check whether the file has changed before allowing an update, instead of habing a lock system when client is editing a file

## struggles
managed to use migrations but still need to understand how they work internally. Planning to watch another explanation.

following design principles (modules, services, controllers...)

not very familiar with using git throughout development.
need to remember to use the git workflow regularly (commit, push, pull, and PRs) 



## Distributed Systems/Dynamo Paper

how distributed storage systems handle data across multiple servers.

### Concepts I Studied

- [x] 1 consistent hashing
- [x] 2 vector clocks
- [x] 3 hinted handoff
- [x] 4 merkel trees
- [x] 5 gossip protocol
- [x] 6 partion and placement


1- instead of stroing one one server, we distribute it across multiple servers using a hash ring.
when we add or remove a server, only some of the data needs to move instead of reorganizing everything.

2- track different versions of data and understand which changes happened before others.
two servers update the same data independently vector clocks can help detect that the versions conflict, and if they edit the same one we can know which is done before the other.

why software systems avoid phyicsal wallclocks
becauae the hardware clock drifts and there are network delays which make wall clocks unreliable 


3- server is temporarily unavailable, another server can hold its data.
omce it back the data is handed over  back to it


4- tree made of hashes that helps servers compare their stored data 
they compare hashes to find which parts are different and need synchronization, instead of comparing everything all together


5- servers communicate with each other to share information about which servers are available, no need ofr a central server


6- partioning (dividing data into smaller groups) and distributing them across servers

use consistent hashing to decide where the data should be stored (placement)

These are the concepts ive understood from dynamo
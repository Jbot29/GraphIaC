# GraphIOC

## Motivation
* Motivation

IOC tools today are centered around building out a service or component. Setting up a DB, some type of compute, etc. Which is great but I think the easier parts of the problem.

The real pain is the connections or edges between things. I need compute that can has permissions and can talk to a db. These are hard to define and manage.

It feels like many of the tools bolt these steps on yet I think it is the most important part. AWS is great but most of my time in it is not spent setting up service but debugging permission errors
while trying to access them.

The goal for GraphIOC is to promote these edge configurations to first class citzens.

A second goal is reusiability. It is a pain setting things up, and you often want to copy and paste basically. Because other systems don't handle the edge configuration well, you can't just copy/paste
and rename. They often have policies and other things all mixed up in the definition that is not easy to tease apart.

Another minor goal is sense we have everything defined as code, why can't the system be somewhat self documenting? Why can't I just see a diagram or generated docs of the the infrastructure?
IOC is good, solved checking in things, envs like stage/prod good.

But it really hasn't it mdae it much easier to create infrastructure.

It is the ORM of infrastructure, easy to do simple things hard to do hard things.

It is easy in to create a db or a S# bucket, it is a hard to glue all the things together. What depends on what and what are the permissions.

Most of my time building infra strcuture is either figure out what needs to be created in what order or why something doesn't have permissions for something.

In Graph terms is it easy to define the nodes but difficult to define the edges. The infrastrucutre is useless without the edges.

Trying to make connections to things seem likes an after thought in the current systems

Because of this it is very difficult to reuse IOC code. The node is a monolith with every attached to it so you can't just copy and paste because it is attached to everything else.

The other things is these tools tend to eat everything. They are the worst types of frameworks as when the break or behave in ways you don't like there is little to be done in the way to workaround them.

IOC tools don't actually give you any knowledge on the platform. Framework problem.

Graphing. Why cant we have up to date graphs and documentation?


First class citzens

Edges
Import
Customization
Full control over behavior
Light weight framewrok

The knownledge lives in the nodes and edges instead of the framework.


Also by having the infra defined as a graph allows secondary analysis of the system. It also allows for easy generation of up to date graphs.




pip install --config-settings="--global-option=build_ext" \
            --config-settings="--global-option=-I$(brew --prefix graphviz)/include/" \
            --config-settings="--global-option=-L$(brew --prefix graphviz)/lib/" \
            pygraphviz
			
			
			
Import -> Pydantic model (chat-gpt?)



python -m GraphIOC --version

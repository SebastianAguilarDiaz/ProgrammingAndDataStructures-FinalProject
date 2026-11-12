#include<iostream>
#include<fstream>
#include <unordered_map>
#include <vector>
#include<set>
#include<stack>
#include<queue>

using namespace std;
const char PATH_CHAR='1';
const char WALL_CHAR='0';
const char ROOT_CHAR='A';
const char TARGET_CHAR='B';
pair<int,int> root_cords;
pair<int,int> target_cords;

vector<pair<int,int>> movements={
    {1,0},
    {0,-1},
    {-1,0},
    {0,1}
};



class Node{
    public:
        Node* parent=nullptr;
        pair<int,int>value;
        vector<Node*> children;

        Node(pair<int,int> v){
            value=v;
        }

        Node* addChildren(Node* c){
            this->children.push_back(c);
            c->parent=this;
            return c;
        }
        Node* addParent(Node* p){
            this->parent=p;
            p->children.push_back(this);
            return p;
        }

        vector<Node*>* ancesters(){
        
            auto p=this->parent;
            auto antrs= new vector<Node*>;
            while(p!=nullptr){
                    antrs->push_back(p);
                p=p->parent;
            }
            return antrs;
        }

        vector<Node*>* preorder(){
            auto res= new vector<Node*>;
            res->push_back(this);
            for(auto child: children){
                auto x=child->preorder();
                for (auto m: *x){
                    res->push_back(m);
                }
            }
            return res; 
        }

        vector<Node*>* postorder(){
            auto res=new vector<Node*>;
            for(auto child:this->children){
                auto x=child->postorder();
                for(auto m:*x)
                    res->push_back(m);
            }
            
            res->push_back(this);
            return res;
        }

        vector<Node*>* bfs(){
            auto res=new vector<Node*>;
            queue<Node*> bfs_q;
            bfs_q.push(this);

            while(bfs_q.size()){
                res->push_back(bfs_q.front());
                for(auto n: bfs_q.front()->children){
                    bfs_q.push(n);
                }
                bfs_q.pop();
            }
            return res;            

        }


        void print(){
            cout<<"("<<value.first<<", "<<value.second<<")";
        }
        string getValue(){
            return to_string(value.first)+" "+to_string(value.second); 
        }

};

vector<vector<char>>* loadMatrix(string file_path);

inline int getNodeNumber(pair<int,int> cords, const int COLS);
inline pair<int,int> getNodeCords( int num, const int COLS);
pair<int,int> findNode(vector<vector<char>>*matrix,const char TARGET);
int getNumOfPathChars(vector<vector<char>>*matrix);
unordered_map<int,Node*>* createTreeBFS(vector<vector<char>> *matrix);
unordered_map<int,Node*>* createTreeDFS(vector<vector<char>>* matrix);
string nodePath(vector<Node*>* path);
void solveMaze (string file_path);
void writeFile(string file_name,vector<Node*>* path);



int main(){
    
    solveMaze("laberinto_1.in");



}










void solveMaze (string file_path) {
    string file_name=file_path.substr(0,file_path.size()-3);
    string extension=".sol";

    auto matrix= loadMatrix(file_path);
    root_cords=findNode(matrix,ROOT_CHAR);
    target_cords=findNode(matrix,TARGET_CHAR);
    int cols=(*matrix)[0].size();
    
    auto treeBFS= createTreeBFS(matrix);
    
    auto treeDFS= createTreeDFS(matrix);
    

    int root_num=getNodeNumber(root_cords,cols);
    auto root_node_BFS=(*treeBFS)[root_num];
    auto root_node_DFS=(*treeDFS)[root_num];
    
    int target_num=getNodeNumber(target_cords,cols);
    auto target_node_BFS=(*treeBFS)[target_num];
    auto target_node_DFS=(*treeDFS)[target_num];
    
    


    auto pre=root_node_DFS->preorder();
    writeFile(file_name+"_preorder"+extension, pre);
    
    auto post=root_node_DFS->postorder();
    writeFile(file_name+"_postorder"+extension, post);
    
    auto ans_dfs=target_node_DFS->ancesters();
    writeFile(file_name+"_dfs_solution"+extension, ans_dfs);
    
    auto b=root_node_BFS->bfs();
    writeFile(file_name+"_bfs"+extension, b);
    
    
    auto ans_bfs=target_node_BFS->ancesters();
    writeFile(file_name+"_bfs_solution"+extension, ans_bfs);

    

}


vector<vector<char>> *loadMatrix(string file_path){
    ifstream fin(file_path);
    int rows, cols;
    fin>>rows>>cols;
    auto m= new vector<vector<char> >(rows,vector<char>(cols));
    for(auto &i: *m){
        for(auto &j:i){
            fin>>j;
        }
    }
    return m;
}

void writeFile( string file_name,vector<Node*>* path){
    auto fout= ofstream(file_name);

    fout<<nodePath(path);
    fout.close();
    

}
string nodePath(vector<Node*>* path){
    string x="";
    for(auto n:*path){
        x += n->getValue()+"\n";
    }

    return x;
}

inline int getNodeNumber(pair<int,int> cords, const int COLS){
    
    if(cords.first<0 || cords.second<0) return -1;
    return cords.first*COLS + cords.second;

}

inline pair<int,int> getNodeCords( int num, const int COLS){

    return {num/COLS,num%COLS};
}




pair<int,int> findNode(vector<vector<char>>*matrix,const char TARGET){

    const int ROWS=(*matrix).size();
    const int COLS=(*matrix)[0].size();

    pair<int,int> root={-1,-1};
    // so we can know where the root node is
    for(int i =0; i < ROWS;i++){
        for(int j =0;j<COLS;j++){   
            if((*matrix)[i][j]==TARGET){
                root={i,j}; 
                break;
            }
        }
    }

    return root;
}




int getNumOfPathChars(vector<vector<char>>*matrix){
    int n=0;
    for(auto i: *matrix){
        for(auto j: i){
            if(j==PATH_CHAR)n++;
        }
    }
    return n;
}





unordered_map<int,Node*>* createTreeBFS(vector<vector<char>> *matrix){
    auto root = root_cords;
    const int COLS=(*matrix)[0].size();
    auto r=getNodeNumber(root,COLS);
    
    auto tree = new unordered_map<int,Node*>;
    tree->insert({r,new Node(root)});

    queue<pair<int,int>> bfs_q;
    bfs_q.push(root);
    set<pair<int,int>>visited;
    visited.insert(root); 
    vector<Node*>parents;
    vector<Node*>children;
    while(bfs_q.size()){
            visited.insert(bfs_q.front());
            
            
            for(auto &n: movements){
                pair<int,int> child={bfs_q.front().first + n.first,bfs_q.front().second+n.second};
                if(visited.find(child)!=visited.end()) continue;
                if(
                    child.first <0||
                    child.second <0||
                    child.first >= (*matrix).size()||
                    child.second >= (*matrix)[0].size()
                        )   continue;
                if((*matrix)[child.first][child.second]==PATH_CHAR
                    ||(*matrix)[child.first][child.second]==TARGET_CHAR
                        ){
                    tree->insert({getNodeNumber(child,COLS),new Node(child)});
                    (*tree)[getNodeNumber(bfs_q.front(),COLS)]->addChildren((*tree)[getNodeNumber(child,COLS)]);
                    bfs_q.push(child);
                }
            }
            bfs_q.pop();
    }
    return tree;
}


unordered_map<int,Node*>* createTreeDFS(vector<vector<char>>* matrix){
    const int COLS=(*matrix)[0].size();
    unordered_map<int,Node*> *tree=new unordered_map<int,Node*>;
    stack<pair<int,int>> dfs_st;
    
    set<pair<int,int>> visited;
    auto r=getNodeNumber(root_cords,COLS);
    tree->insert({r, new Node(root_cords)});
    dfs_st.push(root_cords);

    while(dfs_st.size()){
        auto current=dfs_st.top();
        visited.insert(current);
        dfs_st.pop();

        for(int i=(movements.size()-1);i>=0;i--){
            pair<int,int> child={current.first+movements[i].first,current.second+movements[i].second };
            if(visited.find(child)!=visited.end()) continue;
            if(
                child.first <0||
                child.second <0||
                child.first >= (*matrix).size()||
                child.second >= (*matrix)[0].size()
                    )   continue;
            if((*matrix)[child.first][child.second]==PATH_CHAR
            ||(*matrix)[child.first][child.second]==TARGET_CHAR
            ){
                
                auto childNode=new Node(child);
                tree->insert({getNodeNumber(child,COLS),childNode});
                (*tree)[getNodeNumber(current,COLS)]->addChildren(childNode);
                dfs_st.push(child);
            }
            
        }


    }
    return tree;

}